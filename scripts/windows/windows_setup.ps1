param(
	[string]$PythonCommand = "python",
	[string]$AppPort = "5000",
	[switch]$InstallPythonIfMissing
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path))
$NewScript = Join-Path $ProjectRoot "installer\windows\windows_setup.ps1"

if (-not (Test-Path $NewScript)) {
	throw "新しいセットアップスクリプトが見つかりません: $NewScript"
}

& $NewScript -PythonCommand $PythonCommand -AppPort $AppPort -InstallPythonIfMissing:$InstallPythonIfMissing
