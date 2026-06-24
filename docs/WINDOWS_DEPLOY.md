# Windows Server 配備手順

このプロジェクトは Windows Server 上では `Flask` の開発サーバーではなく、`waitress` を使って起動する前提で準備しています。

補足:
waitress 単体では TLS 終端を行わないため、クライアント接続の HTTPS はサポートしません（HTTPのみ）。
Redmine への外部通信は REDMINE_URL が https の場合に HTTPS になります。

## 必要なもの

- Python 3.11 以上（未導入の場合は `winget` で自動導入可）
- PowerShell 5.1 以上、または PowerShell 7
- インターネット接続、または社内ミラーからの `pip` インストール手段
- Redmine に接続できるネットワーク到達性
- `REDMINE_API_KEY` を設定できる権限

## Python 側の依存関係

- `Flask`
- `requests`
- `waitress`

## セットアップ

1. PowerShell セッションで `REDMINE_URL` / `REDMINE_PROJECT_NAME` / `REDMINE_API_KEY` を設定します。
2. `installer\\windows\\windows_setup.ps1` を実行して仮想環境作成・依存インストール・Redmine初期取得を行います。
3. `scripts\\windows\\Start-WindowsVmEntry.ps1` を実行してアプリを起動します。

Python が未導入の新規サーバーでは、以下のように `-InstallPythonIfMissing` を付けて実行できます（winget 利用）。

```powershell
.\installer\windows\windows_setup.ps1 -InstallPythonIfMissing
```

インストール時専用処理は `installer/` 配下に分離しており、運用時の Web 起動 (`waitress-serve`) では初期化処理を実行しません。

運用起動後に `installer/` を削除してもアプリ起動自体には影響しません。
ただし、再インストール・サービス再登録・同梱アンインストールスクリプト実行には `installer/` が必要です。

## 例

```powershell
$env:REDMINE_URL = "https://jins.cloud.redmine.jp"
$env:REDMINE_PROJECT_NAME = "環境情報"
$env:REDMINE_API_KEY = "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
.
installer\windows\windows_setup.ps1
.
scripts\windows\Start-WindowsVmEntry.ps1 -Host 0.0.0.0 -Port 5000
```

## Windows Service 化 (NSSM)

このプロジェクトのサービス登録は NSSM を利用します。
先に NSSM をインストールしてください。

### NSSM インストール例

powershell:
winget install --id NSSM.NSSM -e

### サービス登録

1. 必要に応じて Machine 環境変数を設定
2. install_windows_service.ps1 を実行

例: filesystem セッションを同時設定して登録

powershell:
.\installer\windows\install_windows_service.ps1 `
  -ServiceName VmEntry `
  -ConfigureMachineEnv `
  -FlaskSecretKey "<LONG_RANDOM_SECRET>" `
  -SessionType filesystem `
  -SessionFileDir "C:\VM-Entry\data\flask_session" `
  -SessionIdleMinutes 60 `
  -StartAfterInstall

例: NSSM の場所を明示して登録

powershell:
.\installer\windows\install_windows_service.ps1 `
  -ServiceName VmEntry `
  -NssmExePath "C:\Users\Administrator\AppData\Local\Microsoft\WinGet\Links\nssm.exe"

### よく使う操作

powershell:
Start-Service -Name VmEntry
Stop-Service -Name VmEntry
Restart-Service -Name VmEntry
Get-Service -Name VmEntry

### サービス削除

powershell:
.\installer\windows\uninstall_windows_service.ps1 -ServiceName VmEntry