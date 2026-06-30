import base64
import html
import re
import uuid

try:
    import winrm
except ImportError:
    winrm = None


_CLIXML_ERROR_RE = re.compile(r'<S S="Error">(.*?)</S>', re.DOTALL)


def _seems_utf16(data):
    if not data:
        return False
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return True
    sample = data[:512]
    if not sample:
        return False
    return sample.count(b"\x00") > max(1, len(sample) // 8)


def _decode_ps_bytes(raw_bytes):
    data = raw_bytes or b""
    encodings = ["utf-8", "cp932", "utf-16-le"]
    if _seems_utf16(data):
        encodings = ["utf-16", "utf-16-le", "utf-8", "cp932"]

    for encoding in encodings:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="ignore")


def _normalize_stderr_text(stderr):
    text = (stderr or "").strip()
    if not text:
        return ""

    if "#< CLIXML" in text:
        matches = _CLIXML_ERROR_RE.findall(text)
        if matches:
            text = "\n".join(html.unescape(match) for match in matches)

    text = text.replace("_x000D__x000A_", "\n")
    text = text.replace("_x000D_", "\n")
    text = text.replace("_x000A_", "\n")
    text = text.replace("`r`n", "\n")
    text = text.replace("`r", "\n")
    return text.strip()


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
    
    def _decode_output(result):
        stdout = _decode_ps_bytes(result.std_out)
        stderr = _normalize_stderr_text(_decode_ps_bytes(result.std_err))
        return stdout, stderr

    utf8_init_result = session.run_ps(
        "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::UTF8; "
        "$OutputEncoding = [System.Text.UTF8Encoding]::UTF8"
    )
    _, utf8_init_stderr = _decode_output(utf8_init_result)
    if utf8_init_result.status_code != 0:
        raise RuntimeError(
            "PowerShell UTF-8 初期化失敗 "
            f"host={host_ip} status={utf8_init_result.status_code} "
            f"stderr={utf8_init_stderr.strip() or '(no stderr)'}"
        )

    script_id = str(uuid.uuid4())
    temp_script_path = f"C:\\Windows\\Temp\\ps_script_{script_id}.ps1"
    temp_b64_path = f"C:\\Windows\\Temp\\ps_script_{script_id}.b64"

    # WinRM のコマンド長制限回避のため、Base64化した本文を分割送信してリモートで再構築する。
    script_payload = script.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")
    script_b64 = base64.b64encode(script_payload.encode("utf-8-sig")).decode("ascii")
    chunk_size = 1200

    init_result = session.run_ps(
        rf"Set-Content -LiteralPath '{temp_b64_path}' -Value '' -Encoding Ascii -Force"
    )
    _, init_stderr = _decode_output(init_result)
    if init_result.status_code != 0:
        raise RuntimeError(
            f"PowerShell スクリプト保存失敗 host={host_ip} status={init_result.status_code} stderr={init_stderr.strip() or '(no stderr)'}"
        )

    try:
        for i in range(0, len(script_b64), chunk_size):
            chunk = script_b64[i : i + chunk_size]
            append_result = session.run_ps(
                rf"Add-Content -LiteralPath '{temp_b64_path}' -Value '{chunk}' -NoNewline -Encoding Ascii"
            )
            _, append_stderr = _decode_output(append_result)
            if append_result.status_code != 0:
                raise RuntimeError(
                    f"PowerShell スクリプト保存失敗 host={host_ip} status={append_result.status_code} stderr={append_stderr.strip() or '(no stderr)'}"
                )

        materialize_result = session.run_ps(
            rf"$b64 = Get-Content -LiteralPath '{temp_b64_path}' -Raw; "
            rf"$bytes = [Convert]::FromBase64String($b64); "
            rf"[System.IO.File]::WriteAllBytes('{temp_script_path}', $bytes)"
        )
        _, materialize_stderr = _decode_output(materialize_result)
        if materialize_result.status_code != 0:
            raise RuntimeError(
                f"PowerShell スクリプト保存失敗 host={host_ip} status={materialize_result.status_code} stderr={materialize_stderr.strip() or '(no stderr)'}"
            )

        result = session.run_ps(rf"& '{temp_script_path}'")
        stdout, stderr = _decode_output(result)

        if result.status_code != 0:
            raise RuntimeError(
                f"PowerShell 実行失敗 host={host_ip} status={result.status_code} stderr={stderr.strip() or '(no stderr)'}"
            )

        return stdout
    finally:
        session.run_ps(
            rf"Remove-Item -LiteralPath '{temp_script_path}','{temp_b64_path}' -Force -ErrorAction SilentlyContinue"
        )
