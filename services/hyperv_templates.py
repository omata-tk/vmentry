import json

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
                merged[value] = label

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

    templates = sorted(merged.items(), key=lambda item: item[0].lower())
    return templates, host_results


def get_vm_templates():
    """
    既存互換用。DB設定から3台分を読み取って取得したい場合は app.py 側で
    get_vm_templates_from_hosts を使ってください。
    """
    return []
