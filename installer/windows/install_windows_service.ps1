param(
	[string]$ServiceName = "VmEntry",
	[string]$DisplayName = "VM Entry Web",
	[string]$Description = "VM Entry application service",
	[string]$ListenHost = "0.0.0.0",
	[int]$Port = 5000,
	[ValidateSet("Automatic", "Manual", "Disabled")]
	[string]$StartupType = "Automatic",
	[switch]$StartAfterInstall
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path))
$RunScript = Join-Path $ProjectRoot "scripts\windows\Run-WindowsVmEntry.ps1"

if (-not (Test-Path $RunScript)) {
	throw "サービス実行スクリプトが見つかりません: $RunScript"
}

if (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue) {
	throw "同名サービスが既に存在します: $ServiceName"
}

$binPath = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$RunScript`" -ListenHost $ListenHost -Port $Port"

New-Service -Name $ServiceName -BinaryPathName $binPath -DisplayName $DisplayName -Description $Description -StartupType $StartupType

# サービス障害時に自動再起動 (60秒待ち)
sc.exe failure $ServiceName reset= 86400 actions= restart/60000 | Out-Null

if ($StartAfterInstall) {
	Start-Service -Name $ServiceName
}

Write-Host "サービス登録が完了しました: $ServiceName"
Write-Host "起動コマンド: Start-Service -Name $ServiceName"
Write-Host "停止コマンド: Stop-Service -Name $ServiceName"
Write-Host "ログインURL: http://localhost:$Port/login"
