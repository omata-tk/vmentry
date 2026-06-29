import json
import os

from core import db
from services.ps_executor import run_ps_on_host


def fetch_templates_from_host(host_ip, username, password):
    script = r"""
$ErrorActionPreference = 'Stop'
Get-VM |
  Where-Object { $_.Name -like 'template*' } |
  Select-Object -ExpandProperty Name |
  ConvertTo-Json -Compress
"""
    stdout = run_ps_on_host(host_ip, username, password, script).strip()
    if not stdout:
        return []

    parsed = json.loads(stdout)
    if isinstance(parsed, str):
        names = [parsed]
    elif isinstance(parsed, list):
        names = [item for item in parsed if isinstance(item, str)]
    else:
        names = []

    return [(name.strip(), name.strip()) for name in names if name and name.strip()]


def _format_host_label(host_name, host_ip):
    label = host_name
    if host_ip:
        label = f"{label} ({host_ip})"
    return label


def fetch_vm_switches_from_host(host_ip, username, password):
    script = r"""
$ErrorActionPreference = 'Stop'
Get-VMSwitch |
  Select-Object -ExpandProperty Name |
  ConvertTo-Json -Compress
"""
    stdout = run_ps_on_host(host_ip, username, password, script).strip()
    if not stdout:
        return []

    parsed = json.loads(stdout)
    if isinstance(parsed, str):
        names = [parsed]
    elif isinstance(parsed, list):
        names = [item for item in parsed if isinstance(item, str)]
    else:
        names = []

    return [name.strip() for name in names if name and name.strip()]


def get_vm_templates_from_hosts(hosts_config):
    """
    複数 Hyper-V ホストからテンプレート一覧を取得。

    Returns:
        (templates, host_results)
        templates: [(vm_name, vm_name), ...]
        host_results: [{host, status, message, count}, ...]
    """
    merged = {}
    host_results = []

    for idx, host in enumerate(hosts_config, start=1):
        host_name = f"Hyper-V ホスト{idx}"
        ip = (host.get("ip") or "").strip()
        user = (host.get("user") or "").strip()
        password_encrypted = (host.get("password") or "").strip()

        password = db._decrypt_password(password_encrypted)
        host_label = _format_host_label(host_name, ip)

        if not ip or not user or not password:
            host_results.append(
                {
                    "host": host_name,
                    "status": "skipped",
                    "count": 0,
                    "message": f"{host_name} は設定未入力のためスキップしました。",
                }
            )
            continue

        try:
            templates = fetch_templates_from_host(ip, user, password)
            for value, label in templates:
                normalized_value = (value or "").strip()
                if not normalized_value:
                    continue

                entry = merged.setdefault(
                    normalized_value,
                    {
                        "label": (label or normalized_value).strip() or normalized_value,
                        "hosts": [],
                    },
                )
                if label and not entry["label"]:
                    entry["label"] = label.strip()
                if host_label not in entry["hosts"]:
                    entry["hosts"].append(host_label)

            host_results.append(
                {
                    "host": host_name,
                    "status": "success",
                    "count": len(templates),
                    "message": f"{host_name} から {len(templates)} 件取得しました。",
                }
            )
        except Exception as exc:
            host_results.append(
                {
                    "host": host_name,
                    "status": "error",
                    "count": 0,
                    "message": f"{host_name} への接続に失敗しました: {str(exc)}",
                }
            )

    templates = []
    for value, data in merged.items():
        host_suffix = " / ".join(data["hosts"])
        display_label = data["label"]
        if host_suffix:
            display_label = f"{display_label} [{host_suffix}]"
        templates.append((value, display_label))

    templates = sorted(templates, key=lambda item: item[0].lower())
    return templates, host_results


def get_vm_switches_from_hosts(hosts_config):
    """
    複数 Hyper-V ホストから仮想スイッチ一覧を取得。

    Returns:
        (switches, host_results)
        switches: [("host|switch", "host|switch"), ...]
        host_results: [{host, status, message, count}, ...]
    """
    merged = {}
    host_results = []

    for idx, host in enumerate(hosts_config, start=1):
        host_name = f"Hyper-V ホスト{idx}"
        ip = (host.get("ip") or "").strip()
        user = (host.get("user") or "").strip()
        password_encrypted = (host.get("password") or "").strip()

        password = db._decrypt_password(password_encrypted)

        if not ip or not user or not password:
            host_results.append(
                {
                    "host": host_name,
                    "status": "skipped",
                    "count": 0,
                    "message": f"{host_name} は設定未入力のためスキップしました。",
                }
            )
            continue

        try:
            switch_names = fetch_vm_switches_from_host(ip, user, password)
            host_label = ip
            for switch_name in switch_names:
                display = f"{host_label}|{switch_name}"
                merged[display] = display

            host_results.append(
                {
                    "host": host_name,
                    "status": "success",
                    "count": len(switch_names),
                    "message": f"{host_name} から仮想スイッチ {len(switch_names)} 件取得しました。",
                }
            )
        except Exception as exc:
            host_results.append(
                {
                    "host": host_name,
                    "status": "error",
                    "count": 0,
                    "message": f"{host_name} への接続に失敗しました: {str(exc)}",
                }
            )

    switches = sorted(merged.items(), key=lambda item: item[0].lower())
    return switches, host_results


def browse_host_path(host_ip, username, password, target_path):
    sanitized_path = (target_path or "X:\\").strip() or "X:\\"
    script = rf"""
$ErrorActionPreference = 'Stop'
$TargetPath = '{sanitized_path.replace("'", "''")}'
if (-not (Test-Path -LiteralPath $TargetPath)) {{
  throw "指定されたパスが存在しません: $TargetPath"
}}
$ResolvedPath = (Resolve-Path -LiteralPath $TargetPath).Path
$ParentPath = Split-Path -Path $ResolvedPath -Parent
$Items = Get-ChildItem -LiteralPath $ResolvedPath -ErrorAction Stop |
  Select-Object Name, FullName, PSIsContainer
@{{
  current_path = $ResolvedPath
  parent_path = $ParentPath
  entries = $Items
}} | ConvertTo-Json -Depth 6 -Compress
"""
    stdout = run_ps_on_host(host_ip, username, password, script).strip()
    if not stdout:
        return {
            "current_path": sanitized_path,
            "parent_path": sanitized_path,
            "entries": [],
        }

    parsed = json.loads(stdout)
    raw_entries = parsed.get("entries", []) if isinstance(parsed, dict) else []
    if isinstance(raw_entries, dict):
        raw_entries = [raw_entries]
    elif not isinstance(raw_entries, list):
        raw_entries = []

    entries = []
    for item in raw_entries:
        if not isinstance(item, dict):
            continue
        name = str(item.get("Name") or "").strip()
        full_path = str(item.get("FullName") or "").strip()
        is_dir = bool(item.get("PSIsContainer"))
        if not name or not full_path:
            continue
        entries.append(
            {
                "name": name,
                "path": full_path,
                "is_dir": is_dir,
                "ext": os.path.splitext(name)[1].lower(),
            }
        )

    entries.sort(key=lambda item: (0 if item["is_dir"] else 1, item["name"].lower()))
    current_path = str(parsed.get("current_path") or sanitized_path) if isinstance(parsed, dict) else sanitized_path
    parent_path = str(parsed.get("parent_path") or current_path) if isinstance(parsed, dict) else current_path

    return {
        "current_path": current_path,
        "parent_path": parent_path,
        "entries": entries,
    }


def get_vm_templates():
    """
    既存互換用。DB設定から3台分を読み取って取得したい場合は app.py 側で
    get_vm_templates_from_hosts を使ってください。
    """
    return []
