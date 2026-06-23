param(
	[string]$PythonCommand = "python",
	[string]$AppPort = "5000",
	[switch]$InstallPythonIfMissing
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path))
$VenvPath = Join-Path $ProjectRoot ".venv"
$ActivatePath = Join-Path $VenvPath "Scripts\Activate.ps1"
$VenvPython = Join-Path $VenvPath "Scripts\python.exe"


function Resolve-PythonLauncher {
	param(
		[string]$PreferredCommand,
		[switch]$TryInstall
	)

	if (Get-Command $PreferredCommand -ErrorAction SilentlyContinue) {
		return @{ Exe = $PreferredCommand; PrefixArgs = @() }
	}

	if (Get-Command "py" -ErrorAction SilentlyContinue) {
		& py -3.11 --version *> $null
		if ($LASTEXITCODE -eq 0) {
			return @{ Exe = "py"; PrefixArgs = @("-3.11") }
		}

		& py -3 --version *> $null
		if ($LASTEXITCODE -eq 0) {
			return @{ Exe = "py"; PrefixArgs = @("-3") }
		}

		& py --version *> $null
		if ($LASTEXITCODE -eq 0) {
			return @{ Exe = "py"; PrefixArgs = @() }
		}
	}

	if ($TryInstall) {
		if (-not (Get-Command "winget" -ErrorAction SilentlyContinue)) {
			throw "Python が見つからず winget も利用できません。事前に Python 3.11+ を導入してください。"
		}

		Write-Host "Python が見つからないため winget で Python 3.11 をインストールします。"
		winget install -e --id Python.Python.3.11 --accept-package-agreements --accept-source-agreements

		if ($LASTEXITCODE -ne 0) {
			throw "winget での Python 導入に失敗しました。手動で Python 3.11+ を導入してください。"
		}

		if (Get-Command "python" -ErrorAction SilentlyContinue) {
			return @{ Exe = "python"; PrefixArgs = @() }
		}
		if (Get-Command "py" -ErrorAction SilentlyContinue) {
			return @{ Exe = "py"; PrefixArgs = @("-3.11") }
		}
	}

	throw "Python 実行環境が見つかりません。-InstallPythonIfMissing を付けるか、Python 3.11+ を導入してください。"
}


$PythonLauncher = Resolve-PythonLauncher -PreferredCommand $PythonCommand -TryInstall:$InstallPythonIfMissing

if (-not (Test-Path $VenvPath)) {
	& $PythonLauncher.Exe @($PythonLauncher.PrefixArgs + @("-m", "venv", $VenvPath))
}

if (-not (Test-Path $ActivatePath)) {
	throw "仮想環境の作成に失敗しました: $ActivatePath"
}

if (-not (Test-Path $VenvPython)) {
	throw "仮想環境の Python 実行ファイルが見つかりません: $VenvPython"
}

& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -r (Join-Path $ProjectRoot "requirements.txt")

if ($env:REDMINE_URL -and $env:REDMINE_PROJECT_NAME -and $env:REDMINE_API_KEY) {
	& $VenvPython -m installer.bootstrap
} else {
	Write-Host "REDMINE_URL / REDMINE_PROJECT_NAME / REDMINE_API_KEY が未設定のため、Redmine初期取得はスキップしました。"
}

Write-Host "セットアップ完了。次は scripts/windows/Start-WindowsVmEntry.ps1 を実行してください。"
