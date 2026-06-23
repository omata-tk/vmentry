# ローカルPCでの動作確認手順

このアプリは、ローカルPCでも最小構成で動作確認できます。

## 確認できること

- フォーム画面の表示
- 入力バリデーション
- Redmine API キー未設定時のエラー表示
- Redmine 連携まで含めた登録処理

## 前提

- Python 3.11 以上
- `pip` が利用可能
- プロジェクトルートでコマンドを実行すること

## 初回セットアップ

### Windows PowerShell

```powershell
cd <プロジェクトのルート>
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### macOS / Linux

```bash
cd <プロジェクトのルート>
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## インストール時にRedmineから初期データを取得する場合

以下を設定してから初期化スクリプトを実行します。

```bash
export REDMINE_URL="https://jins.cloud.redmine.jp"
export REDMINE_PROJECT_NAME="環境情報"
export REDMINE_API_KEY="<your-api-key>"
python -m installer.bootstrap
```

必要に応じて以下も指定できます。

```bash
export DEFAULT_TRACKER_ID="12"
export DEFAULT_STATUS_ID="13"
export DEFAULT_PRIORITY_ID="2"
```

## 画面だけ確認したい場合

Redmine API キーがなくても画面表示までは確認できます。

```bash
python -m web.app
```

ブラウザで `http://127.0.0.1:5000` を開いてください。

## Redmine登録まで確認したい場合

環境変数 `REDMINE_API_KEY` を設定してから起動します。

### Windows PowerShell

```powershell
$env:REDMINE_API_KEY = "<your-api-key>"
python -m web.app
```

### macOS / Linux

```bash
export REDMINE_API_KEY="<your-api-key>"
python -m web.app
```

## 注意点

- 起動コマンドは `python web/app.py` ではなく `python -m web.app` を使ってください。
- `python web/app.py` だと package import の解決に失敗する可能性があります。
- ローカル確認では Flask の開発サーバーで十分です。本番運用では `waitress` を使ってください。