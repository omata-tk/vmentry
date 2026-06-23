try:
    import winrm
except ImportError:
    winrm = None


def run_ps_on_host(host_ip, username, password, script):
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
