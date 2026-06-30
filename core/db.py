import os
import hashlib
import re
import sqlite3
from pathlib import Path


DEFAULT_MASTER_OPTIONS = {}


_VM_TEMPLATE_MANGLE_RE = re.compile(r'^\?+VMEntry_template\?+(.*)', re.DOTALL)


def _normalize_vm_template_option_text(text):
    value = (text or '').strip()
    if not value:
        return value
    if value.startswith('【VMEntry_template】'):
        return value
    m = _VM_TEMPLATE_MANGLE_RE.match(value)
    if m:
        return '【VMEntry_template】' + m.group(1)
    return value


def _repair_vm_template_brackets(conn):
    """起動時マイグレーション: DB内の vm_template 名で【】が??化されたものを修正する。"""
    rows = conn.execute(
        "SELECT option_value, option_label FROM master_options "
        "WHERE category = 'vm_template' "
        "AND option_value LIKE '%VMEntry_template%' "
        "AND option_value NOT LIKE '【VMEntry_template】%'"
    ).fetchall()
    for row in rows:
        old_value = row['option_value']
        old_label = row['option_label']
        m_v = _VM_TEMPLATE_MANGLE_RE.match(old_value)
        new_value = ('【VMEntry_template】' + m_v.group(1)) if m_v else old_value
        m_l = _VM_TEMPLATE_MANGLE_RE.match(old_label)
        new_label = ('【VMEntry_template】' + m_l.group(1)) if m_l else old_label
        conn.execute(
            "UPDATE master_options SET option_value = ?, option_label = ? "
            "WHERE category = 'vm_template' AND option_value = ?",
            (new_value, new_label, old_value),
        )


def _db_path():
    override = os.getenv('VM_ENTRY_DB_PATH', '').strip()
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[1] / 'data' / 'vm_entry.db'


def _connect():
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_by TEXT NOT NULL DEFAULT 'system'
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS master_options (
                category TEXT NOT NULL,
                option_value TEXT NOT NULL,
                option_label TEXT NOT NULL,
                sort_order INTEGER NOT NULL DEFAULT 0,
                is_active INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_by TEXT NOT NULL DEFAULT 'system',
                PRIMARY KEY (category, option_value)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                log_type TEXT NOT NULL DEFAULT 'sync' CHECK(log_type IN ('sync', 'entry')),
                executed_by TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'info' CHECK(status IN ('info', 'error')),
                message TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        # 90日より古いログを削除
        _prune_old_logs(conn, days=90)

        # vm_template 名の【】文字化け修正
        _repair_vm_template_brackets(conn)

        for category, options in DEFAULT_MASTER_OPTIONS.items():
            for sort_order, (option_value, option_label) in enumerate(options):
                conn.execute(
                    """
                    INSERT OR IGNORE INTO master_options (
                        category, option_value, option_label, sort_order, is_active, updated_by
                    ) VALUES (?, ?, ?, ?, 1, 'system')
                    """,
                    (category, option_value, option_label, sort_order),
                )



def is_db_initialized():
    try:
        with _connect() as conn:
            required_tables = {'settings', 'master_options', 'logs'}
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
    except sqlite3.Error:
        return False
    existing = {row['name'] for row in rows}
    return required_tables.issubset(existing)


def assert_runtime_db_ready():
    if is_db_initialized():
        return
    raise RuntimeError(
        'DBが初期化されていません。インストール時セットアップを先に実行してください。'
    )


def get_setting(key, default=''):
    with _connect() as conn:
        row = conn.execute('SELECT value FROM settings WHERE key = ?', (key,)).fetchone()
    if row is None:
        return default
    return row['value']


def get_int_setting(key, default=0):
    raw = (get_setting(key, '') or '').strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def set_settings(values, updated_by='system'):
    with _connect() as conn:
        for key, value in values.items():
            conn.execute(
                """
                INSERT INTO settings (key, value, updated_by)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value=excluded.value,
                    updated_at=CURRENT_TIMESTAMP,
                    updated_by=excluded.updated_by
                """,
                (key, '' if value is None else str(value), updated_by),
            )


def get_all_settings():
    with _connect() as conn:
        rows = conn.execute('SELECT key, value FROM settings ORDER BY key').fetchall()
    return {row['key']: row['value'] for row in rows}


def get_master_options(category):
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT option_value, option_label
            FROM master_options
            WHERE category = ? AND is_active = 1
            ORDER BY sort_order ASC, option_label ASC
            """,
            (category,),
        ).fetchall()
    if category == 'vm_template':
        return [
            (
                _normalize_vm_template_option_text(row['option_value']),
                _normalize_vm_template_option_text(row['option_label']),
            )
            for row in rows
        ]
    return [(row['option_value'], row['option_label']) for row in rows]


def replace_master_options(category, options, updated_by='system'):
    with _connect() as conn:
        conn.execute(
            "UPDATE master_options SET is_active = 0, updated_at = CURRENT_TIMESTAMP, updated_by = ? WHERE category = ?",
            (updated_by, category),
        )
        for sort_order, (option_value, option_label) in enumerate(options):
            if category == 'vm_template':
                option_value = _normalize_vm_template_option_text(option_value)
                option_label = _normalize_vm_template_option_text(option_label)
            conn.execute(
                """
                INSERT INTO master_options (
                    category, option_value, option_label, sort_order, is_active, updated_by
                ) VALUES (?, ?, ?, ?, 1, ?)
                ON CONFLICT(category, option_value) DO UPDATE SET
                    option_label=excluded.option_label,
                    sort_order=excluded.sort_order,
                    is_active=1,
                    updated_at=CURRENT_TIMESTAMP,
                    updated_by=excluded.updated_by
                """,
                (category, str(option_value), str(option_label), sort_order, updated_by),
            )


def get_assignee_name_to_id():
    result = {}
    for option_value, option_label in get_master_options('assignee'):
        try:
            result[option_label] = int(option_value)
        except ValueError:
            continue
    return result


def get_vhost_ip_display_map():
    return {option_value: option_label for option_value, option_label in get_master_options('vhost')}


def get_subnet_vlan_map():
    result = {}
    for subnet_prefix, vlan_text in get_master_options('subnet_vlan'):
        subnet = (subnet_prefix or '').strip()
        vlan = (vlan_text or '').strip()
        if not subnet or not vlan:
            continue
        result[subnet] = vlan
    return result


def get_subnet_gateway_map():
    result = {}
    for subnet_prefix, gateway_text in get_master_options('subnet_gateway'):
        subnet = (subnet_prefix or '').strip()
        gateway = (gateway_text or '').strip()
        if not subnet or not gateway:
            continue
        result[subnet] = gateway
    return result


def get_subnet_dns_map():
    result = {}
    for subnet_prefix, dns_text in get_master_options('subnet_dns'):
        subnet = (subnet_prefix or '').strip()
        dns = (dns_text or '').strip()
        if not subnet or not dns:
            continue
        result[subnet] = dns
    return result


def get_hidden_subnet_set():
    hidden = set()
    for option_value, _ in get_master_options('subnet_hidden'):
        subnet = (option_value or '').strip()
        if subnet:
            hidden.add(subnet)
    return hidden


def get_visible_subnet_options():
    hidden = get_hidden_subnet_set()
    return [
        (option_value, option_label)
        for option_value, option_label in get_master_options('subnet')
        if (option_value or '').strip() not in hidden
    ]


def get_vlan_id_for_subnet(subnet_prefix):
    subnet = (subnet_prefix or '').strip()
    if not subnet:
        return ''
    return get_subnet_vlan_map().get(subnet, '')


def get_gateway_for_subnet(subnet_prefix):
    subnet = (subnet_prefix or '').strip()
    if not subnet:
        return ''
    return get_subnet_gateway_map().get(subnet, '')


def get_dns_for_subnet(subnet_prefix):
    subnet = (subnet_prefix or '').strip()
    if not subnet:
        return ''
    return get_subnet_dns_map().get(subnet, '')


def _normalize_log_type(log_type):
    return 'entry' if (log_type or '').strip().lower() == 'entry' else 'sync'


def _normalize_status(status):
    # 正常終了のみ info、それ以外は error
    return 'info' if (status or '').strip().lower() == 'info' else 'error'


def _status_from_legacy(status):
    # 旧呼び出し互換: success/info/ok は info、それ以外は error
    s = (status or '').strip().lower()
    return 'info' if s in ('success', 'info', 'ok') else 'error'


def _prune_old_logs(conn, days=90):
    conn.execute(
        "DELETE FROM logs WHERE created_at < datetime('now', ?)",
        (f'-{int(days)} days',),
    )


def prune_old_logs(days=90):
    with _connect() as conn:
        _prune_old_logs(conn, days=days)


def append_log(log_type, executed_by, status='info', message=''):
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO logs (log_type, executed_by, status, message)
            VALUES (?, ?, ?, ?)
            """,
            (
                _normalize_log_type(log_type),
                (executed_by or 'Unknown User').strip() or 'Unknown User',
                _normalize_status(status),
                '' if message is None else str(message),
            ),
        )
        _prune_old_logs(conn, days=90)


def get_recent_logs(limit=20, log_type=None):
    safe_limit = max(1, int(limit))
    with _connect() as conn:
        if log_type:
            rows = conn.execute(
                """
                SELECT created_at, log_type, executed_by, status, message
                FROM logs
                WHERE log_type = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (_normalize_log_type(log_type), safe_limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT created_at, log_type, executed_by, status, message
                FROM logs
                ORDER BY id DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
    return [dict(row) for row in rows]


# 互換API（既存呼び出しを壊さない）
def append_sync_log(executed_by, status, message=''):
    append_log('sync', executed_by, _status_from_legacy(status), message)


def get_recent_sync_logs(limit=20):
    return get_recent_logs(limit=limit, log_type='sync')


def _hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()

def _get_encryption_key():
    """暗号化キーを環境変数から取得。デフォルトはアプリケーション用の固定値"""
    from cryptography.fernet import Fernet
    
    # 環境変数から取得、なければデフォルト値を使用
    key_str = os.getenv('ENCRYPTION_KEY', '').strip()
    if not key_str:
        # デフォルト：'system'を基にしたFernetキーを生成
        base = hashlib.sha256(b'vm-entry-default-key').digest()
        key_str = __import__('base64').b64encode(base).decode('utf-8')[:44] + '='
    
    try:
        # 32バイト（256ビット）のキーが必要な場合はこちら
        if len(key_str) == 44 and key_str.endswith('='):
            return key_str
        # そうでない場合はBase64デコードして確認
        return key_str
    except Exception:
        raise ValueError("ENCRYPTION_KEY の形式が無効です")


def _encrypt_password(raw_password: str) -> str:
    """パスワードをAES暗号化して返す"""
    from cryptography.fernet import Fernet
    
    raw = (raw_password or '').strip()
    if not raw:
        return ''
    
    key = _get_encryption_key()
    f = Fernet(key)
    encrypted = f.encrypt(raw.encode('utf-8'))
    return encrypted.decode('utf-8')


def _decrypt_password(encrypted_password: str) -> str:
    """暗号化されたパスワードを復号して返す"""
    from cryptography.fernet import Fernet, InvalidToken
    
    encrypted = (encrypted_password or '').strip()
    if not encrypted:
        return ''
    
    try:
        key = _get_encryption_key()
        f = Fernet(key)
        decrypted = f.decrypt(encrypted.encode('utf-8'))
        return decrypted.decode('utf-8')
    except (InvalidToken, Exception):
        # 復号に失敗した場合は空文字列を返す
        return ''


def set_admin_key(raw_key: str) -> None:
    """管理者キーをハッシュ化してDBに保存する"""
    raw = (raw_key or '').strip()
    if not raw:
        return
    set_settings({'admin_magic_key': _hash_key(raw)}, updated_by='system')


def is_admin_key(raw_text):
    stored = get_setting('admin_magic_key', '')
    return bool(stored) and _hash_key((raw_text or '').strip()) == stored


# ──────────────────────────────────────────────
# テンプレートごとの Sysprep 認証情報
# ──────────────────────────────────────────────

def get_template_sysprep_credentials():
    """
    登録済みのテンプレート認証情報を返す。
    Returns: [(template_name, os_user, os_password_encrypted), ...]
    """
    users = {v: l for v, l in get_master_options('template_sysprep_user')}
    passwords = {v: l for v, l in get_master_options('template_sysprep_password')}
    result = []
    for template_name, os_user in users.items():
        result.append({
            'template_name': template_name,
            'os_user': os_user,
            'os_password': _decrypt_password(passwords.get(template_name, '')),
        })
    return result


def get_template_sysprep_user(template_name):
    """テンプレート名に対応した OS ユーザーを返す（なければ空文字）。"""
    users = dict(get_master_options('template_sysprep_user'))
    return users.get((template_name or '').strip(), '')


def get_template_sysprep_password(template_name):
    """テンプレート名に対応した OS パスワードを返す（復号済み、なければ空文字）。"""
    passwords = dict(get_master_options('template_sysprep_password'))
    encrypted = passwords.get((template_name or '').strip(), '')
    return _decrypt_password(encrypted)


def replace_template_sysprep_credentials(rows, updated_by='system'):
    """
    テンプレート認証情報を一括置換する。
    rows: [{'template_name': str, 'os_user': str, 'os_password': str}, ...]
    """
    user_options = []
    password_options = []
    for row in rows:
        name = (row.get('template_name') or '').strip()
        user = (row.get('os_user') or '').strip()
        raw_password = (row.get('os_password') or '').strip()
        if not name:
            continue
        user_options.append((name, user))
        encrypted = _encrypt_password(raw_password) if raw_password else ''
        password_options.append((name, encrypted))
    replace_master_options('template_sysprep_user', user_options, updated_by=updated_by)
    replace_master_options('template_sysprep_password', password_options, updated_by=updated_by)
