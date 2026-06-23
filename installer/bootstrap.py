"""Install-time bootstrap flow.

This module must only be used during installation or initial provisioning.
"""

import os

from core import db
from services import redmine


REQUIRED_ENV_VARS = [
    'REDMINE_URL',
    'REDMINE_PROJECT_NAME',
    'REDMINE_API_KEY',
]

DEFAULT_INSTALL_SETTING_VALUES = {
    'redmine_url': '',
    'project_name': '',
    'default_tracker_id': '12',
    'default_status_id': '13',
    'default_priority_id': '2',
    'default_start_octet': '2',
    'form_default_subject': '新規環境',
    'form_default_os_user': 'Administrator',
    'admin_magic_key': 'jkadmin',
    'admin_username': 'jkadmin',
}


def _require_env(name):
    value = os.getenv(name, '').strip()
    if not value:
        raise RuntimeError(f'{name} が未設定です。')
    return value


def run_installation_bootstrap():
    db.init_db()

    redmine_url = _require_env('REDMINE_URL')
    project_name = _require_env('REDMINE_PROJECT_NAME')
    api_key = _require_env('REDMINE_API_KEY')

    env_overrides = {
        'default_tracker_id': os.getenv('DEFAULT_TRACKER_ID', '').strip(),
        'default_status_id': os.getenv('DEFAULT_STATUS_ID', '').strip(),
        'default_priority_id': os.getenv('DEFAULT_PRIORITY_ID', '').strip(),
        'default_start_octet': os.getenv('DEFAULT_START_OCTET', '').strip(),
        'form_default_subject': os.getenv('FORM_DEFAULT_SUBJECT', '').strip(),
        'form_default_os_user': os.getenv('FORM_DEFAULT_OS_USER', '').strip(),
        'admin_magic_key': os.getenv('ADMIN_MAGIC_KEY', '').strip(),
        'admin_username': os.getenv('ADMIN_USERNAME', '').strip(),
    }

    settings_payload = {
        'redmine_url': redmine_url,
        'project_name': project_name,
    }
    # admin_magic_key はハッシュ化して保存するため、通常ループから除外する
    for key, default_value in DEFAULT_INSTALL_SETTING_VALUES.items():
        if key in ('redmine_url', 'project_name', 'admin_magic_key'):
            continue
        settings_payload[key] = env_overrides.get(key) or db.get_setting(key, default_value)

    db.set_settings(settings_payload, updated_by='bootstrap')
    raw_key = env_overrides.get('admin_magic_key') or ''
    if not raw_key and not db.get_setting('admin_magic_key', ''):
        raw_key = DEFAULT_INSTALL_SETTING_VALUES['admin_magic_key']
    if raw_key:
        db.set_admin_key(raw_key)

    latest = redmine.fetch_latest_form_master(project_name, api_key=api_key)

    db.replace_master_options(
        'subnet',
        [(value, label) for value, label in latest.get('subnet_prefixes', [])] if latest.get('subnet_prefixes') and isinstance(latest.get('subnet_prefixes')[0], tuple) else [(value, value) for value in latest.get('subnet_prefixes', [])],
        updated_by='bootstrap',
    )
    db.replace_master_options(
        'os',
        latest.get('os_options', []),
        updated_by='bootstrap',
    )
    db.replace_master_options(
        'usage',
        latest.get('usage_options', []),
        updated_by='bootstrap',
    )
    db.replace_master_options(
        'vhost',
        [(value, label) for value, label in latest.get('vhost_ip_display_map', {}).items()],
        updated_by='bootstrap',
    )
    db.replace_master_options(
        'assignee',
        [(str(user_id), name) for name, user_id in latest.get('assignee_name_to_id', {}).items()],
        updated_by='bootstrap',
    )
    db.append_sync_log('bootstrap', 'success', 'インストール時のRedmine初期取得に成功しました。')

    return latest


def main():
    latest = run_installation_bootstrap()
    print(
        '初期化完了: '
        f"subnet {len(latest.get('subnet_prefixes', []))} 件, "
        f"os {len(latest.get('os_options', []))} 件, "
        f"usage {len(latest.get('usage_options', []))} 件, "
        f"vhost {len(latest.get('vhost_ip_display_map', {}))} 件, "
        f"assignee {len(latest.get('assignee_name_to_id', {}))} 件"
    )


if __name__ == '__main__':
    main()
