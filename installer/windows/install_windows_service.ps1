param(
    [string]$ServiceName = "VmEntry",
    [string]$DisplayName = "VM Entry Web",
    [string]$Description = "VM Entry application service",
    [string]$ListenHost = "0.0.0.0",
    [int]$Port = 5000,
    [ValidateSet("Automatic", "Manual", "Disabled")]
    [string]$StartupType = "Automatic",
    [switch]$StartAfterInstall,

    # サービス登録時に Machine 環境変数を設定する場合に指定
    [switch]$ConfigureMachineEnv,

    # [app.py](http://_vscodecontentref_/5) が参照する設定
    [string]$FlaskSecretKey,
    [ValidateSet("filesystem", "redis")]
    [string]$SessionType = "filesystem",
    [string]$SessionFileDir = "",
    [int]$SessionIdleMinutes = 60,
    [string]$SessionRedisUrl = "",

    # NSSM 実行ファイル。未指定時は PATH から解決
    [string]$NssmExePath = ""
)

$ErrorActionPreference = "Stop"

function Set-MachineEnv {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [string]$Value,
        [switch]$Required
    )

    if ($Required -and [string]::IsNullOrWhiteSpace($Value)) {
        throw "必須パラメータが未指定です: $Name"
    }

    if (-not [string]::IsNullOrWhiteSpace($Value)) {
        [Environment]::SetEnvironmentVariable($Name, $Value, "Machine")
    }
}

function Resolve-NssmExe {
    param([string]$SpecifiedPath)

    if (-not [string]::IsNullOrWhiteSpace($SpecifiedPath)) {
        if (-not (Test-Path $SpecifiedPath)) {
            throw "指定された NSSM 実行ファイルが見つかりません: $SpecifiedPath"
        }
        return (Resolve-Path $SpecifiedPath).Path
    }

    $cmd = Get-Command nssm -ErrorAction SilentlyContinue
    if ($cmd) {
        return $cmd.Source
    }

    throw @"
nssm.exe が見つかりません。
先に NSSM をインストールしてください。
例: winget install --id NSSM.NSSM -e
"@
}

function Convert-StartupTypeToNssm {
    param([string]$Type)

    switch ($Type) {
        "Automatic" { return "SERVICE_AUTO_START" }
        "Manual"    { return "SERVICE_DEMAND_START" }
        "Disabled"  { return "SERVICE_DISABLED" }
        default     { throw "未対応の StartupType: $Type" }
    }
}

$ProjectRoot = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path))
$WaitressExe = Join-Path $ProjectRoot ".venv\Scripts\waitress-serve.exe"
$LogDir = Join-Path $ProjectRoot "data\log"
$StdOutLog = Join-Path $LogDir "vm_entry_service.out.log"
$StdErrLog = Join-Path $LogDir "vm_entry_service.err.log"

if (-not (Test-Path $WaitressExe)) {
    throw "waitress-serve.exe が見つかりません: $WaitressExe`n先に windows_setup.ps1 を実行してください。"
}

if (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue) {
    throw "同名サービスが既に存在します: $ServiceName"
}

$NssmExe = Resolve-NssmExe -SpecifiedPath $NssmExePath

if ($ConfigureMachineEnv) {
    if ([string]::IsNullOrWhiteSpace($SessionFileDir) -and $SessionType -eq "filesystem") {
        $SessionFileDir = Join-Path $ProjectRoot "data\flask_session"
    }

    if ($SessionType -eq "redis" -and [string]::IsNullOrWhiteSpace($SessionRedisUrl)) {
        throw "SessionType=redis の場合は SessionRedisUrl が必須です。"
    }

    if ($SessionType -eq "filesystem") {
        New-Item -ItemType Directory -Path $SessionFileDir -Force | Out-Null
    }

    Set-MachineEnv -Name "FLASK_SECRET_KEY" -Value $FlaskSecretKey -Required
    Set-MachineEnv -Name "SESSION_TYPE" -Value $SessionType -Required
    Set-MachineEnv -Name "SESSION_FILE_DIR" -Value $SessionFileDir
    Set-MachineEnv -Name "SESSION_IDLE_MINUTES" -Value $SessionIdleMinutes.ToString() -Required
    Set-MachineEnv -Name "SESSION_COOKIE_SECURE" -Value "0" -Required
    Set-MachineEnv -Name "SESSION_REDIS_URL" -Value $SessionRedisUrl
}

New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

# 1) サービス作成
& $NssmExe install $ServiceName $WaitressExe

# 2) 実行パラメータ
& $NssmExe set $ServiceName AppParameters "--host=$ListenHost --port=$Port web.app:app"
& $NssmExe set $ServiceName AppDirectory $ProjectRoot

# 3) 表示名・説明・起動種別
& $NssmExe set $ServiceName DisplayName $DisplayName
& $NssmExe set $ServiceName Description $Description
& $NssmExe set $ServiceName Start (Convert-StartupTypeToNssm -Type $StartupType)

# 4) 出力ログ
& $NssmExe set $ServiceName AppStdout $StdOutLog
& $NssmExe set $ServiceName AppStderr $StdErrLog
& $NssmExe set $ServiceName AppRotateFiles 1
& $NssmExe set $ServiceName AppRotateOnline 1
& $NssmExe set $ServiceName AppRotateBytes 10485760

# 5) 異常終了時は再起動
& $NssmExe set $ServiceName AppExit Default Restart

if ($StartAfterInstall) {
    Start-Service -Name $ServiceName
}

Write-Host "NSSM でサービス登録が完了しました: $ServiceName"
if ($ConfigureMachineEnv) {
    Write-Host "Machine 環境変数を更新しました（新規プロセス/サービス再起動で反映）。"
}
Write-Host "起動コマンド: Start-Service -Name $ServiceName"
Write-Host "停止コマンド: Stop-Service -Name $ServiceName"
Write-Host "ログインURL: http://localhost:$Port/login"
Write-Host "標準出力ログ: $StdOutLog"
Write-Host "標準エラーログ: $StdErrLog"