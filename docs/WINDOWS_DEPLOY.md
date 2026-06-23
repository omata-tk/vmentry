# Windows Server 配備手順

このプロジェクトは Windows Server 上では `Flask` の開発サーバーではなく、`waitress` を使って起動する前提で準備しています。

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

## Windows Service 化

常駐運用する場合は、インストール済み環境でサービス登録スクリプトを実行します。

1. サービス実行ユーザーで参照できる `REDMINE_API_KEY` を設定します。
2. サービスを登録します。

```powershell
[Environment]::SetEnvironmentVariable("REDMINE_API_KEY", "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx", "Machine")
.\installer\windows\install_windows_service.ps1 -ServiceName VmEntry -Port 5000 -StartAfterInstall
```

よく使う操作:

```powershell
Start-Service -Name VmEntry
Stop-Service -Name VmEntry
Restart-Service -Name VmEntry
Get-Service -Name VmEntry
```

サービス削除:

```powershell
.\installer\windows\uninstall_windows_service.ps1 -ServiceName VmEntry
```

## アンインストール

アプリを完全に削除する場合は、次の順で実行します。

1. サービス運用中なら停止して削除

```powershell
Stop-Service -Name VmEntry -ErrorAction SilentlyContinue
.\installer\windows\uninstall_windows_service.ps1 -ServiceName VmEntry
```

2. Machine 環境変数から API キーを削除（必要に応じて URL/プロジェクト名も削除）

```powershell
[Environment]::SetEnvironmentVariable("REDMINE_API_KEY", $null, "Machine")
[Environment]::SetEnvironmentVariable("REDMINE_URL", $null, "Machine")
[Environment]::SetEnvironmentVariable("REDMINE_PROJECT_NAME", $null, "Machine")
```

3. 配備フォルダを削除

```powershell
Remove-Item -Recurse -Force <配備フォルダパス>
```

補足: DB を残したい場合は `<配備フォルダ>\\data\\vm_entry.db` を退避してから削除してください。

## 追加で必要になる可能性があるもの

- Hyper-V 連携を実装する段階では、PowerShell の Hyper-V モジュール、WinRM、実行対象ホストへの管理権限が必要になります。
- 画面を外部公開するなら、IIS のリバースプロキシや社内 LB の前段配置も検討してください。