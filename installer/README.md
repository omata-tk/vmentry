# Installer Folder

このフォルダはインストール時にのみ必要なコードを配置します。
運用時のアプリ起動には使いません。

## 含まれるもの

- `bootstrap.py`: 初回DB初期化と Redmine からの初期データ取得
- `windows/windows_setup.ps1`: Windows Server 向けインストール処理
- `windows/install_windows_service.ps1`: Windows Service 登録処理
- `windows/uninstall_windows_service.ps1`: Windows Service 削除処理

## 削除可否

- 運用起動 (`scripts/windows/Start-WindowsVmEntry.ps1`) だけなら、インストール完了後に `installer/` を削除しても動作します。
- ただし、再インストール・サービス再登録・同梱アンインストールスクリプト実行には `installer/` が必要です。

## 実行例

```powershell
$env:REDMINE_URL = "https://example.redmine.jp"
$env:REDMINE_PROJECT_NAME = "環境情報"
$env:REDMINE_API_KEY = "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
.
installer\windows\windows_setup.ps1
```

## サービス登録時に環境変数を同時設定する

注意:
クライアントから本アプリへの接続は HTTP のみサポートします。
本構成は waitress 単体起動のため、HTTPS 待受は行いません。
Redmine との通信は REDMINE_URL が https の場合に HTTPS で行われます。

サービス実行時に参照される設定のうち、起動必須のものは Machine 環境変数として保持できます。
install_windows_service.ps1 は -ConfigureMachineEnv 指定時に以下を設定できます。

- FLASK_SECRET_KEY
- SESSION_TYPE
- SESSION_FILE_DIR
- SESSION_IDLE_MINUTES
- SESSION_REDIS_URL

注意:
- REDMINE_URL
- REDMINE_PROJECT_NAME
- REDMINE_API_KEY

これら 3 項目は setup 時の bootstrap に使用します。
windows_setup.ps1 実行時に指定し、bootstrap 完了後は redmine_url / project_name が DB に保存されます。

### 例: filesystem セッション

~~~powershell
.\installer\windows\install_windows_service.ps1 `
  -ServiceName VmEntry `
  -ConfigureMachineEnv `
  -FlaskSecretKey "<LONG_RANDOM_SECRET>" `
  -SessionType filesystem `
  -SessionFileDir "C:\VMEntry\data\flask_session" `
  -SessionIdleMinutes 60 `
  -StartAfterInstall
~~~