import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services import redmine
from core import db


def main():
    db.assert_runtime_db_ready()
    api_key = os.getenv('REDMINE_API_KEY', '').strip()
    if not api_key:
        raise RuntimeError('REDMINE_API_KEY が未設定です。環境変数で設定してください。')

    project_id = redmine.get_redmine_project_id(db.get_setting('project_name', ''), api_key=api_key)
    if project_id is None:
        raise RuntimeError('プロジェクトIDが取得できませんでした。')

    latest_options = redmine.get_custom_field_options_batch_from_redmine(
        [redmine.CUSTOM_FIELD_OS_ID, redmine.CUSTOM_FIELD_USAGE_ID],
        api_key=api_key,
        project_id=project_id,
    )
    os_options = latest_options.get(redmine.CUSTOM_FIELD_OS_ID, [])
    usage_options = latest_options.get(redmine.CUSTOM_FIELD_USAGE_ID, [])
    redmine.update_master_options(os_options, usage_options, updated_by='script')

    print(f'更新完了: OS {len(os_options)} 件, 利用用途 {len(usage_options)} 件')


if __name__ == '__main__':
    main()
