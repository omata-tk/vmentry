param(
	[string]$ServiceName = "VmEntry"
)

$ErrorActionPreference = "Stop"

$svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if (-not $svc) {
	Write-Host "サービスは存在しません: $ServiceName"
	exit 0
}

if ($svc.Status -ne "Stopped") {
	Stop-Service -Name $ServiceName -Force
}

sc.exe delete $ServiceName | Out-Null
Write-Host "サービスを削除しました: $ServiceName"
