# Redmine登録処理を読み込む
from services import redmine

results = redmine.create_tickets()

print('\n--- 登録結果サマリー ---')
success = [r for r in results if r.get('result') == 'success']
failed  = [r for r in results if r.get('result') == 'error']
skipped = [r for r in results if r.get('result') == 'skipped']
"""Redmine登録のCLIエントリーポイント。"""

from services import redmine


def main():
    results = redmine.create_tickets()

    print('\n--- 登録結果サマリー ---')
    success = [r for r in results if r.get('result') == 'success']
    failed = [r for r in results if r.get('result') == 'error']
    skipped = [r for r in results if r.get('result') == 'skipped']

    print(f'成功: {len(success)} 件')
    for r in success:
        print(f'  チケットID: {r["id"]}, IPアドレス: {r["ip"]}')

    if failed:
        print(f'失敗: {len(failed)} 件')
        for r in failed:
            print(f'  サブネット: {r["subnet"]}, エラー: {r.get("message", "")}')

    if skipped:
        print(f'スキップ: {len(skipped)} 件')
        for r in skipped:
            print(f'  {r["message"]}')


if __name__ == '__main__':
    main()

print(f'成功: {len(success)} 件')
for r in success:
    print(f'  チケットID: {r["id"]}, IPアドレス: {r["ip"]}')

if failed:
    print(f'失敗: {len(failed)} 件')
    for r in failed:
        print(f'  サブネット: {r["subnet"]}, エラー: {r.get("message", "")}')

if skipped:
    print(f'スキップ: {len(skipped)} 件')
    for r in skipped:
        print(f'  {r["message"]}')
