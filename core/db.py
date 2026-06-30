import os
import hashlib
import sqlite3
from pathlib import Path


DEFAULT_MASTER_OPTIONS = {}


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
    return [(row['option_value'], row['option_label']) for row in rows]


def replace_master_options(category, options, updated_by='system'):
    with _connect() as conn:
        conn.execute(
            "UPDATE master_options SET is_active = 0, updated_at = CURRENT_TIMESTAMP, updated_by = ? WHERE category = ?",
            (updated_by, category),
        )
        for sort_order, (option_value, option_label) in enumerate(options):
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
