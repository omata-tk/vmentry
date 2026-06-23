param(
	[string]$ListenHost = "0.0.0.0",
	[int]$Port = 5000
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path))
$WaitressExe = Join-Path $ProjectRoot ".venv\Scripts\waitress-serve.exe"

if (-not (Test-Path $WaitressExe)) {
	throw "waitress-serve.exe が見つかりません。先に installer/windows/windows_setup.ps1 を実行してください。"
}

$env:PYTHONUNBUFFERED = "1"
Set-Location $ProjectRoot

& $WaitressExe "--host=$ListenHost" "--port=$Port" "web.app:app"
