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
