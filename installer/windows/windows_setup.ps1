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


function Resolve-RequiredEnvValue {
    param(
        [Parameter(Mandatory = $true)][string]$Name
    )

    $value = (Get-Item -Path "Env:$Name" -ErrorAction SilentlyContinue).Value
    if (-not [string]::IsNullOrWhiteSpace($value)) {
        return $value.Trim()
    }

    $machine = [Environment]::GetEnvironmentVariable($Name, "Machine")
    if (-not [string]::IsNullOrWhiteSpace($machine)) {
        return $machine.Trim()
    }

    throw "bootstrap 必須のため $Name を設定してください。"
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

$redmineUrl = Resolve-RequiredEnvValue -Name "REDMINE_URL"
$projectName = Resolve-RequiredEnvValue -Name "REDMINE_PROJECT_NAME"
$apiKey = Resolve-RequiredEnvValue -Name "REDMINE_API_KEY"

$env:REDMINE_URL = $redmineUrl
$env:REDMINE_PROJECT_NAME = $projectName
$env:REDMINE_API_KEY = $apiKey

& $VenvPython -m installer.bootstrap

Write-Host "セットアップ完了。DB初期化および Redmine 初期取得を実行しました。"
