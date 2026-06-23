import json

try:
    import winrm
except ImportError:
    winrm = None


def create_virtual_machine(request_data):
    """将来の Hyper-V 作成処理を実装する。"""
    raise NotImplementedError("Hyper-V 作成処理は未実装です。")


def _run_ps_on_host(host_ip, username, password, script):
    if winrm is None:
        raise RuntimeError(
            "pywinrm がインストールされていません。requirements.txt に pywinrm を追加して再インストールしてください。"
        )

    endpoint = f"http://{host_ip}:5985/wsman"
    session = winrm.Session(
        target=endpoint,
        auth=(username, password),
        transport="ntlm",
        server_cert_validation="ignore",
    )
    result = session.run_ps(script)

    stdout = (result.std_out or b"").decode("utf-8", errors="ignore")
    stderr = (result.std_err or b"").decode("utf-8", errors="ignore")

    if result.status_code != 0:
        raise RuntimeError(
            f"PowerShell 実行失敗 host={host_ip} status={result.status_code} stderr={stderr.strip() or '(no stderr)'}"
        )

    return stdout


def _fetch_templates_from_host(host_ip, username, password):
    # VM名のみ取得。テンプレート判定は template*。
    script = r"""
$ErrorActionPreference = 'Stop'
Get-VM |
  Where-Object { $_.Name -like 'template*' } |
  Select-Object -ExpandProperty Name |
  ConvertTo-Json -Compress
"""
    stdout = _run_ps_on_host(host_ip, username, password, script).strip()
    if not stdout:
        return []

    parsed = json.loads(stdout)
    if isinstance(parsed, str):
        names = [parsed]
    elif isinstance(parsed, list):
        names = [item for item in parsed if isinstance(item, str)]
    else:
        names = []

    # value, label とも VM名
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
        ip = (host.get("ip") or "").strip()
        user = (host.get("user") or "").strip()
        password = (host.get("password") or "").strip()
        host_name = f"host{idx}:{ip or '(empty)'}"

        # いずれか空欄なら接続しない
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
            templates = _fetch_templates_from_host(ip, user, password)
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