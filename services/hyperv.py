from services.hyperv_templates import (
    browse_host_path,
    fetch_templates_from_host,
    get_vm_switches_from_hosts,
    get_vm_templates,
    get_vm_templates_from_hosts,
)
from services.ps_executor import run_ps_on_host


def _normalize_host_ip(value):
    text = (value or "").strip()
    if not text:
        return ""
    text = text.replace("（Hyper-v）", "").replace("(Hyper-v)", "")
    text = text.replace("（Hyper-V）", "").replace("(Hyper-V)", "")
    return text.strip()


def _pick_target_host(vm_host, hosts):
    normalized_vm_host = _normalize_host_ip(vm_host)
    for host in hosts or []:
        ip = _normalize_host_ip(host.get("ip") or "")
        user = (host.get("user") or "").strip()
        password = (host.get("password") or "").strip()
        if not ip or not user or not password:
            continue
        if ip == normalized_vm_host:
            return host
    return None


def _pick_clone_host(vm_host, clone_host, hosts):
    for candidate in (clone_host, vm_host):
        target = _pick_target_host(candidate, hosts)
        if target:
            return target
    return None


def _resolve_clone_context(request_data):
  deploy_type = (request_data.get("deploy_type") or "").strip()
  vm_template = (request_data.get("vm_template") or "").strip()
  vm_name = (request_data.get("vm_name") or "").strip()
  vm_host = (request_data.get("vhost_ip") or "").strip()
  clone_host_ip = (request_data.get("clone_host_ip") or "").strip()

  if deploy_type != "template":
    raise RuntimeError("現在はテンプレートからの複製のみ対応しています。")
  if not vm_template:
    raise RuntimeError("VMテンプレートが指定されていません。")
  if not vm_name:
    raise RuntimeError("仮想マシン名が指定されていません。")
  if not vm_host:
    raise RuntimeError("複製先Hyper-Vホストが指定されていません。")

  target_host = _pick_clone_host(vm_host, clone_host_ip, request_data.get("hosts") or [])
  if not target_host:
    raise RuntimeError(f"複製先Hyper-Vホストが見つかりません: {clone_host_ip or vm_host}")

  host_ip = (target_host.get("ip") or "").strip()
  host_user = (target_host.get("user") or "").strip()
  host_password = (target_host.get("password") or "").strip()

  return {
    "vm_template": vm_template,
    "vm_name": vm_name,
    "host_ip": host_ip,
    "host_user": host_user,
    "host_password": host_password,
  }


def _normalize_vlan_id(vlan_id):
  text = (vlan_id or "").strip()
  if not text:
    return ""
  try:
    vlan_int = int(text)
  except ValueError:
    raise RuntimeError("VLAN IDは数値で入力してください。")
  if vlan_int < 1 or vlan_int > 4094:
    raise RuntimeError("VLAN IDは1から4094の範囲で入力してください。")
  return str(vlan_int)


def precheck_virtual_machine(request_data):
  context = _resolve_clone_context(request_data)
  vm_template = context["vm_template"]
  vm_name = context["vm_name"]
  host_ip = context["host_ip"]
  host_user = context["host_user"]
  host_password = context["host_password"]

  templates = fetch_templates_from_host(host_ip, host_user, host_password)
  template_names = {value for value, _ in templates}
  if template_names and vm_template not in template_names:
    raise RuntimeError(f"選択ホスト上にテンプレートが存在しません: {vm_template}")

  safe_vm_name = vm_name.replace("'", "''")
  precheck_script = rf"""
$ErrorActionPreference = 'Stop'
$NewVmName = '{safe_vm_name}'
$existingVm = Get-VM -Name $NewVmName -ErrorAction SilentlyContinue
if ($existingVm) {{
  throw "同名のVMが既に存在します: $NewVmName"
}}
"""
  run_ps_on_host(host_ip, host_user, host_password, precheck_script)


def _build_clone_script(vm_template, vm_name, vlan_id="", iso_path=""):
    safe_template = vm_template.replace("'", "''")
    safe_vm_name = vm_name.replace("'", "''")
    safe_vlan_id = (vlan_id or "").strip()
    safe_iso_path = (iso_path or "").strip().replace("'", "''")
    return rf"""
$ErrorActionPreference = 'Stop'
$TemplateName = '{safe_template}'
$NewVmName = '{safe_vm_name}'
$VlanId = '{safe_vlan_id}'
$IsoPath = '{safe_iso_path}'

$existingVm = Get-VM -Name $NewVmName -ErrorAction SilentlyContinue
if ($existingVm) {{
  throw "同名のVMが既に存在します: $NewVmName"
}}

$templateVm = Get-VM -Name $TemplateName -ErrorAction SilentlyContinue
if (-not $templateVm) {{
  throw "テンプレートVMが見つかりません: $TemplateName"
}}

$templateDisk = Get-VMHardDiskDrive -VMName $TemplateName -ErrorAction Stop | Select-Object -First 1
if (-not $templateDisk -or -not $templateDisk.Path) {{
  throw "テンプレートVMのディスク情報が取得できません: $TemplateName"
}}

$sourceVhdPath = $templateDisk.Path
$sourceDir = Split-Path -Path $sourceVhdPath -Parent
$templateVmPath = $templateVm.Path
if (-not $templateVmPath) {{
  $templateVmPath = $sourceDir
}}

$destRootDir = Split-Path -Path $templateVmPath -Parent
if (-not $destRootDir) {{
  throw "テンプレートVMの配置先ディレクトリが取得できません: $TemplateName"
}}

$destDir = Join-Path -Path $destRootDir -ChildPath $NewVmName
if (-not (Test-Path -LiteralPath $destDir)) {{
  New-Item -Path $destDir -ItemType Directory -Force | Out-Null
}}

$destVhdDir = Join-Path -Path $destDir -ChildPath 'Virtual Hard Disks'
if (-not (Test-Path -LiteralPath $destVhdDir)) {{
  New-Item -Path $destVhdDir -ItemType Directory -Force | Out-Null
}}

$destVhdPath = Join-Path -Path $destVhdDir -ChildPath ($NewVmName + '.vhdx')
if (Test-Path -LiteralPath $destVhdPath) {{
  throw "複製先VHDXが既に存在します: $destVhdPath"
}}

$createdVm = $false
$copiedDisk = $false
try {{
  Copy-Item -LiteralPath $sourceVhdPath -Destination $destVhdPath -Force -ErrorAction Stop
  $copiedDisk = $true

  New-VM -Name $NewVmName -Generation $templateVm.Generation -VHDPath $destVhdPath -Path $destRootDir -ErrorAction Stop | Out-Null
  $createdVm = $true

  $templateProc = Get-VMProcessor -VMName $TemplateName -ErrorAction SilentlyContinue
  if ($templateProc) {{
    Set-VMProcessor -VMName $NewVmName -Count $templateProc.Count -ErrorAction Stop
  }}

  $templateMemory = Get-VMMemory -VMName $TemplateName -ErrorAction SilentlyContinue
  if ($templateMemory) {{
    if ($templateMemory.DynamicMemoryEnabled) {{
      Set-VMMemory -VMName $NewVmName `
        -DynamicMemoryEnabled $true `
        -StartupBytes $templateMemory.Startup `
        -MinimumBytes $templateMemory.Minimum `
        -MaximumBytes $templateMemory.Maximum `
        -ErrorAction Stop
    }} else {{
      Set-VMMemory -VMName $NewVmName `
        -DynamicMemoryEnabled $false `
        -StartupBytes $templateMemory.Startup `
        -ErrorAction Stop
    }}
  }}

  $templateNic = Get-VMNetworkAdapter -VMName $TemplateName -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($templateNic -and $templateNic.SwitchName) {{
    Connect-VMNetworkAdapter -VMName $NewVmName -SwitchName $templateNic.SwitchName -ErrorAction Stop
  }}

  if ($VlanId) {{
    Set-VMNetworkAdapterVlan -VMName $NewVmName -Access -VlanId $VlanId -ErrorAction Stop
  }}

  if ($IsoPath) {{
    $dvd = Add-VMDvdDrive -VMName $NewVmName -ErrorAction Stop -Passthru
    if (-not (Test-Path -LiteralPath $IsoPath)) {{
      throw "ISOイメージファイルが見つかりません: $IsoPath"
    }}
    Set-VMDvdDrive -VMName $NewVmName -ControllerNumber $dvd.ControllerNumber `
      -ControllerLocation $dvd.ControllerLocation -Path $IsoPath -ErrorAction Stop
  }}
}}
catch {{
  if ($createdVm) {{
    Remove-VM -Name $NewVmName -Force -ErrorAction SilentlyContinue
  }}
  if ($copiedDisk -and (Test-Path -LiteralPath $destVhdPath)) {{
    Remove-Item -LiteralPath $destVhdPath -Force -ErrorAction SilentlyContinue
  }}
  if (Test-Path -LiteralPath $destDir) {{
    $left = Get-ChildItem -LiteralPath $destDir -Force -ErrorAction SilentlyContinue
    if (-not $left) {{
      Remove-Item -LiteralPath $destDir -Force -ErrorAction SilentlyContinue
    }}
  }}
  throw
}}

@{{
  vm_name = $NewVmName
  template_name = $TemplateName
  source_vhd = $sourceVhdPath
  cloned_vhd = $destVhdPath
}} | ConvertTo-Json -Compress
"""


def create_virtual_machine(request_data):
    context = _resolve_clone_context(request_data)
    vm_template = context["vm_template"]
    vm_name = context["vm_name"]
    host_ip = context["host_ip"]
    host_user = context["host_user"]
    host_password = context["host_password"]
    vlan_id = _normalize_vlan_id(request_data.get("vlan_id"))
    iso_path = (request_data.get("template_iso_path") or "").strip() if request_data.get("template_iso_mount") else ""

    templates = fetch_templates_from_host(host_ip, host_user, host_password)
    template_names = {value for value, _ in templates}
    if template_names and vm_template not in template_names:
        raise RuntimeError(f"選択ホスト上にテンプレートが存在しません: {vm_template}")

    script = _build_clone_script(vm_template, vm_name, vlan_id=vlan_id, iso_path=iso_path)
    stdout = run_ps_on_host(host_ip, host_user, host_password, script).strip()
    return {
        "result": "success",
        "host": host_ip,
        "vm_name": vm_name,
        "template": vm_template,
        "details": stdout,
    }
