from services.hyperv_templates import (
    browse_host_path,
    fetch_templates_from_host,
    get_vm_switches_from_hosts,
    get_vm_templates,
    get_vm_templates_from_hosts,
)
import re

from core import db
from services.ps_executor import run_ps_on_host


_VM_NAME_MAX_LENGTH = 255
_VM_NAME_INVALID_CHAR_RE = re.compile(r'[`$\"]')


def validate_vm_name(vm_name):
  normalized = (vm_name or "").strip()
  if not normalized:
    return "仮想マシン名は必須です。"
  if len(normalized) > _VM_NAME_MAX_LENGTH:
    return f"仮想マシン名は{_VM_NAME_MAX_LENGTH}文字以内で入力してください。"
  if _VM_NAME_INVALID_CHAR_RE.search(normalized):
    return "仮想マシン名に次の文字は使用できません: ` $ \""
  if any(ord(ch) < 32 for ch in normalized):
    return "仮想マシン名に制御文字は使用できません。"
  return ""


def _notify_progress(progress_callback, stage_code, message):
  if progress_callback:
    progress_callback(stage_code, message)


def _is_enabled(value):
  if value is True:
    return True
  return str(value or "").strip().lower() in ("1", "true", "yes", "on")


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
  vm_name_error = validate_vm_name(vm_name)
  if vm_name_error:
    raise RuntimeError(f"仮想マシン名が不正です: {vm_name_error}")
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


def _get_guest_credentials(request_data):
  vm_template = (request_data.get("vm_template") or "").strip()
  guest_user = db.get_template_sysprep_user(vm_template)
  guest_password = db.get_template_sysprep_password(vm_template)
  if not guest_user:
    raise RuntimeError(
      f"テンプレート「{vm_template}」の Sysprep 認証情報が登録されていません。"
      "管理画面の「テンプレート Sysprep 認証情報」で設定してください。"
    )
  if not guest_password:
    raise RuntimeError(
      f"テンプレート「{vm_template}」の Sysprep パスワードが登録されていません。"
      "管理画面の「テンプレート Sysprep 認証情報」で設定してください。"
    )
  return guest_user, guest_password


def _get_confirmed_ip(request_data):
  confirmed_ip = (request_data.get("confirmed_ip") or "").strip()
  if not confirmed_ip:
    raise RuntimeError("Sysprep 後のIPアドレス設定に必要な割当予定IPが取得できません。")
  return confirmed_ip


def _get_subnet_network_settings(request_data):
  subnet_prefix = (request_data.get("target_subnet") or "").strip()
  if not subnet_prefix:
    confirmed_ip = (request_data.get("confirmed_ip") or "").strip()
    if confirmed_ip.count(".") == 3:
      subnet_prefix = ".".join(confirmed_ip.split(".")[:3])

  gateway = db.get_gateway_for_subnet(subnet_prefix)
  dns_servers = db.get_dns_for_subnet(subnet_prefix)
  return gateway, dns_servers


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
$OutputEncoding = [System.Text.UTF8Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::UTF8
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
  $OutputEncoding = [System.Text.UTF8Encoding]::UTF8
  [Console]::OutputEncoding = [System.Text.UTF8Encoding]::UTF8
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


def _build_start_vm_and_wait_for_guest_script(vm_name, guest_user, guest_password):
    safe_vm_name = vm_name.replace("'", "''")
    safe_guest_user = guest_user.replace("'", "''")
    safe_guest_password = guest_password.replace("'", "''")
    return rf"""
$ErrorActionPreference = 'Stop'
  $OutputEncoding = [System.Text.UTF8Encoding]::UTF8
  [Console]::OutputEncoding = [System.Text.UTF8Encoding]::UTF8
  $StageLabel = 'vm_boot'
$NewVmName = '{safe_vm_name}'
$GuestUser = '{safe_guest_user}'
$GuestPassword = '{safe_guest_password}'

$vm = Get-VM -Name $NewVmName -ErrorAction SilentlyContinue
if (-not $vm) {{
  $knownVms = Get-VM -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name
  $knownVmText = if ($knownVms) {{ $knownVms -join ',' }} else {{ '(none)' }}
  throw "Sysprep 対象VMが見つかりません stage=$StageLabel vm=$NewVmName host=$env:COMPUTERNAME known_vms=$knownVmText"
}}

if ($vm.State -ne 'Running') {{
  Start-VM -Name $NewVmName -ErrorAction Stop | Out-Null
}}

$securePassword = ConvertTo-SecureString $GuestPassword -AsPlainText -Force
$credential = New-Object System.Management.Automation.PSCredential ($GuestUser, $securePassword)

$deadline = (Get-Date).AddMinutes(15)
$connected = $false
$lastError = ''
while ((Get-Date) -lt $deadline) {{
  try {{
    Invoke-Command -VMName $NewVmName -Credential $credential -ScriptBlock {{ 'ready' }} -ErrorAction Stop | Out-Null
    @{{
      vm_name = $NewVmName
      guest = 'ready'
    }} | ConvertTo-Json -Compress
    return
  }}
  catch {{
    $lastError = $_.Exception.Message
    Start-Sleep -Seconds 10
  }}
}}

if (-not $connected) {{
  throw "ゲストOSへの接続待機がタイムアウトしました: $lastError"
}}
"""


def _build_run_sysprep_and_wait_for_shutdown_script(vm_name, guest_user, guest_password):
    safe_vm_name = vm_name.replace("'", "''")
    safe_guest_user = guest_user.replace("'", "''")
    safe_guest_password = guest_password.replace("'", "''")
    return rf"""
$ErrorActionPreference = 'Stop'
  $OutputEncoding = [System.Text.UTF8Encoding]::UTF8
  [Console]::OutputEncoding = [System.Text.UTF8Encoding]::UTF8
  $StageLabel = 'sysprep'
$NewVmName = '{safe_vm_name}'
$GuestUser = '{safe_guest_user}'
$GuestPassword = '{safe_guest_password}'

$vm = Get-VM -Name $NewVmName -ErrorAction SilentlyContinue
if (-not $vm) {{
  $knownVms = Get-VM -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name
  $knownVmText = if ($knownVms) {{ $knownVms -join ',' }} else {{ '(none)' }}
  throw "Sysprep 対象VMが見つかりません stage=$StageLabel vm=$NewVmName host=$env:COMPUTERNAME known_vms=$knownVmText"
}}

$securePassword = ConvertTo-SecureString $GuestPassword -AsPlainText -Force
$credential = New-Object System.Management.Automation.PSCredential ($GuestUser, $securePassword)

Invoke-Command -VMName $NewVmName -Credential $credential -ErrorAction Stop -ScriptBlock {{
  $sysprepPath = Join-Path $env:WINDIR 'System32\Sysprep\Sysprep.exe'
  if (-not (Test-Path -LiteralPath $sysprepPath)) {{
    throw "Sysprep が見つかりません: $sysprepPath"
  }}
  Start-Process -FilePath $sysprepPath -ArgumentList '/oobe /generalize /shutdown /quiet' -WindowStyle Hidden
}}

$shutdownDeadline = (Get-Date).AddMinutes(20)
while ((Get-Date) -lt $shutdownDeadline) {{
  $vmState = (Get-VM -Name $NewVmName -ErrorAction Stop).State
  if ($vmState -eq 'Off') {{
    @{{
      vm_name = $NewVmName
      sysprep = 'completed'
    }} | ConvertTo-Json -Compress
    return
  }}
  Start-Sleep -Seconds 10
}}

throw "Sysprep 実行後の停止待機がタイムアウトしました: $NewVmName"
"""


def _build_prepare_post_sysprep_script(vm_name, guest_user, guest_password, iso_path=""):
    safe_vm_name = vm_name.replace("'", "''")
    safe_guest_user = guest_user.replace("'", "''")
    safe_guest_password = guest_password.replace("'", "''")
    safe_iso_path = (iso_path or "").strip().replace("'", "''")
    return rf"""
$ErrorActionPreference = 'Stop'
  $OutputEncoding = [System.Text.UTF8Encoding]::UTF8
  [Console]::OutputEncoding = [System.Text.UTF8Encoding]::UTF8
  $StageLabel = 'vm_restart'
$NewVmName = '{safe_vm_name}'
$GuestUser = '{safe_guest_user}'
$GuestPassword = '{safe_guest_password}'
$IsoPath = '{safe_iso_path}'

$vm = Get-VM -Name $NewVmName -ErrorAction SilentlyContinue
if (-not $vm) {{
  $knownVms = Get-VM -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name
  $knownVmText = if ($knownVms) {{ $knownVms -join ',' }} else {{ '(none)' }}
  throw "post-Sysprep 対象VMが見つかりません stage=$StageLabel vm=$NewVmName host=$env:COMPUTERNAME known_vms=$knownVmText"
}}

if ($IsoPath) {{
  if (-not (Test-Path -LiteralPath $IsoPath)) {{
    throw "ISOイメージファイルが見つかりません: $IsoPath"
  }}

  $dvd = Get-VMDvdDrive -VMName $NewVmName -ErrorAction SilentlyContinue | Select-Object -First 1
  if (-not $dvd) {{
    $dvd = Add-VMDvdDrive -VMName $NewVmName -ErrorAction Stop -Passthru
  }}

  Set-VMDvdDrive -VMName $NewVmName -ControllerNumber $dvd.ControllerNumber `
    -ControllerLocation $dvd.ControllerLocation -Path $IsoPath -ErrorAction Stop
}}

if ((Get-VM -Name $NewVmName -ErrorAction Stop).State -ne 'Running') {{
  Start-VM -Name $NewVmName -ErrorAction Stop | Out-Null
}}

$securePassword = ConvertTo-SecureString $GuestPassword -AsPlainText -Force
$credential = New-Object System.Management.Automation.PSCredential ($GuestUser, $securePassword)

$deadline = (Get-Date).AddMinutes(20)
$connected = $false
$lastError = ''
while ((Get-Date) -lt $deadline) {{
  try {{
    Invoke-Command -VMName $NewVmName -Credential $credential -ScriptBlock {{ 'ready' }} -ErrorAction Stop | Out-Null
    @{{
      vm_name = $NewVmName
      guest = 'ready'
      iso_mounted = [bool]$IsoPath
    }} | ConvertTo-Json -Compress
    return
  }}
  catch {{
    $lastError = $_.Exception.Message
    Start-Sleep -Seconds 10
  }}
}}

if (-not $connected) {{
  throw "post-Sysprep のゲスト接続待機がタイムアウトしました: $lastError"
}}
"""


def _build_apply_post_sysprep_settings_script(vm_name, guest_user, guest_password, confirmed_ip, gateway="", dns_servers=""):
    safe_vm_name = vm_name.replace("'", "''")
    safe_guest_user = guest_user.replace("'", "''")
    safe_guest_password = guest_password.replace("'", "''")
    safe_confirmed_ip = confirmed_ip.replace("'", "''")
    safe_gateway = (gateway or "").strip().replace("'", "''")
    safe_dns_servers = (dns_servers or "").strip().replace("'", "''")
    return rf"""
$ErrorActionPreference = 'Stop'
  $OutputEncoding = [System.Text.UTF8Encoding]::UTF8
  [Console]::OutputEncoding = [System.Text.UTF8Encoding]::UTF8
  $StageLabel = 'os_config'
$NewVmName = '{safe_vm_name}'
$GuestUser = '{safe_guest_user}'
$GuestPassword = '{safe_guest_password}'
$ConfirmedIp = '{safe_confirmed_ip}'
$Gateway = '{safe_gateway}'
$DnsServers = '{safe_dns_servers}'

$vm = Get-VM -Name $NewVmName -ErrorAction SilentlyContinue
if (-not $vm) {{
  $knownVms = Get-VM -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name
  $knownVmText = if ($knownVms) {{ $knownVms -join ',' }} else {{ '(none)' }}
  throw "post-Sysprep 対象VMが見つかりません stage=$StageLabel vm=$NewVmName host=$env:COMPUTERNAME known_vms=$knownVmText"
}}

$securePassword = ConvertTo-SecureString $GuestPassword -AsPlainText -Force
$credential = New-Object System.Management.Automation.PSCredential ($GuestUser, $securePassword)

$renameResult = Invoke-Command -VMName $NewVmName -Credential $credential -ErrorAction Stop -ScriptBlock {{
  param($ConfiguredIp, $ConfiguredGateway, $ConfiguredDnsServers, $DesiredComputerName)

  # セットアップウィザード中の場合、OOBE をスキップ
  @(
    'HKLM:\Software\Microsoft\Windows\CurrentVersion\OOBE',
    'HKLM:\Software\Policies\Microsoft\Windows\System'
  ) | ForEach-Object {{
    if (Test-Path -LiteralPath $_) {{
      Set-ItemProperty -Path $_ -Name 'SkipMachineOOBE' -Value 1 -Type DWord -Force -ErrorAction SilentlyContinue
      Set-ItemProperty -Path $_ -Name 'SkipUserOOBE' -Value 1 -Type DWord -Force -ErrorAction SilentlyContinue
    }}
  }}

  # OOBE ウィザード完了を待つ（最大3分）
  $oobeWaitDeadline = (Get-Date).AddMinutes(3)
  $oobeCompleted = $false
  while ((Get-Date) -lt $oobeWaitDeadline) {{
    try {{
      $oobeDone = (Get-ItemProperty -Path 'HKLM:\Software\Microsoft\Windows\CurrentVersion\OOBE' -Name 'OOBECompleted' -ErrorAction SilentlyContinue).OOBECompleted
      if ($oobeDone -eq 1) {{
        $oobeCompleted = $true
        break
      }}
    }} catch {{}}
    Start-Sleep -Seconds 5
  }}

  $adapter = Get-NetAdapter -ErrorAction Stop |
    Where-Object {{ $_.Status -ne 'Disabled' -and $_.HardwareInterface }} |
    Sort-Object ifIndex |
    Select-Object -First 1
  if (-not $adapter) {{
    throw '有効なネットワークアダプターが見つかりません。'
  }}

  $existingAddresses = Get-NetIPAddress -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object {{ $_.IPAddress -ne $ConfiguredIp -and $_.IPAddress -notlike '169.254.*' }}
  foreach ($address in $existingAddresses) {{
    Remove-NetIPAddress -InterfaceIndex $adapter.ifIndex -IPAddress $address.IPAddress -Confirm:$false -ErrorAction SilentlyContinue
  }}

  $existingTarget = Get-NetIPAddress -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object {{ $_.IPAddress -eq $ConfiguredIp }}
  if (-not $existingTarget) {{
    Set-NetIPInterface -InterfaceIndex $adapter.ifIndex -Dhcp Disabled -ErrorAction Stop
    
    if ($ConfiguredGateway) {{
      New-NetIPAddress -InterfaceIndex $adapter.ifIndex -IPAddress $ConfiguredIp -PrefixLength 24 -DefaultGateway $ConfiguredGateway -AddressFamily IPv4 -ErrorAction Stop | Out-Null
    }} else {{
      New-NetIPAddress -InterfaceIndex $adapter.ifIndex -IPAddress $ConfiguredIp -PrefixLength 24 -AddressFamily IPv4 -ErrorAction Stop | Out-Null
    }}
  }}

  if ($ConfiguredDnsServers) {{
    $dnsList = $ConfiguredDnsServers.Split(',') | ForEach-Object {{ $_.Trim() }} | Where-Object {{ $_ }}
    if ($dnsList.Count -gt 0) {{
      Set-DnsClientServerAddress -InterfaceIndex $adapter.ifIndex -ServerAddresses $dnsList -ErrorAction Stop
    }}
  }}

  # RDP を有効化
  Set-ItemProperty -Path 'HKLM:\System\CurrentControlSet\Control\Terminal Server' -Name 'fDenyTSConnections' -Value 0 -ErrorAction Stop
  Get-NetFirewallRule | Where-Object {{ $_.Service -eq 'TermService' }} -ErrorAction SilentlyContinue | Enable-NetFirewallRule -ErrorAction SilentlyContinue | Out-Null

  # コンピュータ名の処理
  $normalizedName = ($DesiredComputerName | ForEach-Object {{ ($_ -as [string]) }}).Trim()
  if (-not $normalizedName) {{
    throw 'コンピュータ名に使用する仮想マシン名が空です。'
  }}
  $normalizedName = $normalizedName -replace '[\\/:*?"<>|]', '-'
  $normalizedName = $normalizedName.Trim().Trim('.')
  if (-not $normalizedName) {{
    throw 'コンピュータ名に使用できる文字がありません。'
  }}

  # Unicode の結合文字を壊さないよう、テキスト要素単位で15文字へ切り詰める。
  if ([System.Globalization.StringInfo]::new($normalizedName).LengthInTextElements -gt 15) {{
    $indexes = [System.Globalization.StringInfo]::ParseCombiningCharacters($normalizedName)
    if ($indexes.Length -gt 15) {{
      $normalizedName = $normalizedName.Substring(0, $indexes[15])
    }}
  }}

  $currentName = ([System.Net.Dns]::GetHostName() | ForEach-Object {{ ($_ -as [string]) }}).Trim()
  $computerNameNeedsRestart = $false
  if ($currentName -ne $normalizedName) {{
    Rename-Computer -NewName $normalizedName -Force -ErrorAction Stop
    $computerNameNeedsRestart = $true
  }}

  @{{
    computer_name = $normalizedName
    computer_name_restart_required = [bool]$computerNameNeedsRestart
    oobe_completed = [bool]$oobeCompleted
    ip_address = $ConfiguredIp
    gateway = $ConfiguredGateway
    dns_servers = $ConfiguredDnsServers
  }}
}} -ArgumentList $ConfirmedIp, $Gateway, $DnsServers, $NewVmName

@{{
  vm_name = $NewVmName
  ip_address = $ConfirmedIp
  gateway = $Gateway
  dns = $DnsServers
  remote_desktop = 'enabled'
  computer_name = $renameResult.computer_name
  computer_name_restart_required = [bool]$renameResult.restart_required
}} | ConvertTo-Json -Compress
"""


def _build_verify_vm_exists_script(vm_name):
    safe_vm_name = vm_name.replace("'", "''")
    return rf"""
$ErrorActionPreference = 'Stop'
$OutputEncoding = [System.Text.UTF8Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::UTF8
$NewVmName = '{safe_vm_name}'

$deadline = (Get-Date).AddMinutes(2)
while ((Get-Date) -lt $deadline) {{
  $vm = Get-VM -Name $NewVmName -ErrorAction SilentlyContinue
  if ($vm) {{
    @{{
      vm_name = $NewVmName
      vm_state = [string]$vm.State
      verify = 'ok'
    }} | ConvertTo-Json -Compress
    return
  }}
  Start-Sleep -Seconds 5
}}

$knownVms = Get-VM -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name
$knownVmText = if ($knownVms) {{ $knownVms -join ',' }} else {{ '(none)' }}
throw "複製後のVM存在確認に失敗しました stage=vm_verify vm=$NewVmName host=$env:COMPUTERNAME known_vms=$knownVmText"
"""


def _run_vm_stage(host_ip, host_user, host_password, stage_code, vm_name, script, progress_callback, message):
  _notify_progress(progress_callback, stage_code, message)
  try:
    return run_ps_on_host(host_ip, host_user, host_password, script).strip()
  except Exception as exc:
    error = f"stage={stage_code} vm={vm_name} host={host_ip} error={exc}"
    db.append_log("entry", "system", "error", f"vm stage failed {error}")
    raise RuntimeError(error) from exc


def create_virtual_machine(request_data, progress_callback=None):
  context = _resolve_clone_context(request_data)
  vm_template = context["vm_template"]
  vm_name = context["vm_name"]
  vm_name_error = validate_vm_name(vm_name)
  if vm_name_error:
    raise RuntimeError(f"仮想マシン名が不正です: {vm_name_error}")
  host_ip = context["host_ip"]
  host_user = context["host_user"]
  host_password = context["host_password"]
  vlan_id = _normalize_vlan_id(request_data.get("vlan_id"))
  iso_path = (request_data.get("template_iso_path") or "").strip() if request_data.get("template_iso_mount") else ""
  sysprep_enabled = _is_enabled(request_data.get("sysprep_enabled"))

  templates = fetch_templates_from_host(host_ip, host_user, host_password)
  template_names = {value for value, _ in templates}
  if template_names and vm_template not in template_names:
    raise RuntimeError(f"選択ホスト上にテンプレートが存在しません: {vm_template}")

  clone_iso_path = "" if sysprep_enabled else iso_path
  script = _build_clone_script(vm_template, vm_name, vlan_id=vlan_id, iso_path=clone_iso_path)
  stdout = _run_vm_stage(
    host_ip,
    host_user,
    host_password,
    "vm_clone",
    vm_name,
    script,
    progress_callback,
    "テンプレートから仮想マシンを複製しています。",
  )
  progress_outputs = [stdout] if stdout else []

  verify_stdout = _run_vm_stage(
    host_ip,
    host_user,
    host_password,
    "vm_verify",
    vm_name,
    _build_verify_vm_exists_script(vm_name),
    progress_callback,
    "複製後の仮想マシン存在確認をしています。",
  )
  if verify_stdout:
    progress_outputs.append(verify_stdout)

  if sysprep_enabled:
    guest_user, guest_password = _get_guest_credentials(request_data)
    confirmed_ip = _get_confirmed_ip(request_data)
    gateway, dns_servers = _get_subnet_network_settings(request_data)

    start_vm_stdout = _run_vm_stage(
      host_ip,
      host_user,
      host_password,
      "vm_boot",
      vm_name,
      _build_start_vm_and_wait_for_guest_script(vm_name, guest_user, guest_password),
      progress_callback,
      "仮想マシンを起動し、Sysprep 実行前の接続を待っています。",
    )
    if start_vm_stdout:
      progress_outputs.append(start_vm_stdout)

    sysprep_stdout = _run_vm_stage(
      host_ip,
      host_user,
      host_password,
      "sysprep",
      vm_name,
      _build_run_sysprep_and_wait_for_shutdown_script(vm_name, guest_user, guest_password),
      progress_callback,
      "Sysprep を実行し、シャットダウン完了を待っています。",
    )
    if sysprep_stdout:
      progress_outputs.append(sysprep_stdout)

    prepare_post_stdout = _run_vm_stage(
      host_ip,
      host_user,
      host_password,
      "vm_restart",
      vm_name,
      _build_prepare_post_sysprep_script(
        vm_name,
        guest_user,
        guest_password,
        iso_path=iso_path,
      ),
      progress_callback,
      "仮想マシンを再起動し、ゲストOSの再接続を待っています。",
    )
    if prepare_post_stdout:
      progress_outputs.append(prepare_post_stdout)

    apply_settings_stdout = _run_vm_stage(
      host_ip,
      host_user,
      host_password,
      "os_config",
      vm_name,
      _build_apply_post_sysprep_settings_script(
      vm_name,
      guest_user,
      guest_password,
      confirmed_ip,
      gateway=gateway,
      dns_servers=dns_servers,
      ),
      progress_callback,
      "Sysprep 後の OS 設定を適用しています。",
    )
    if apply_settings_stdout:
      progress_outputs.append(apply_settings_stdout)

  details = "\n".join(item for item in progress_outputs if item).strip()

  return {
    "result": "success",
    "host": host_ip,
    "vm_name": vm_name,
    "template": vm_template,
    "details": details,
  }
