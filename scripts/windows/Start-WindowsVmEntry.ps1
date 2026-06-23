param(
	[string]$ListenHost = "0.0.0.0",
	[int]$Port = 5000
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path))
$ActivatePath = Join-Path $ProjectRoot ".venv\Scripts\Activate.ps1"

if (-not (Test-Path $ActivatePath)) {
	throw "仮想環境が見つかりません。先に installer/windows/windows_setup.ps1 を実行してください。"
}

. $ActivatePath

$env:PYTHONUNBUFFERED = "1"

waitress-serve --host=$ListenHost --port=$Port web.app:app