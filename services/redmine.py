# Redmineにチケットを登録するためのプログラム
import requests
from core import db
import json
import re
import csv
import io

CUSTOM_FIELD_IP_ID = 52  # IPアドレスのカスタムフィールドID
CUSTOM_FIELD_VHOST_IP_ID = 53  # IPアドレス（仮想ホスト）のカスタムフィールドID
CUSTOM_FIELD_OS_ID = 43  # OS のカスタムフィールドID
CUSTOM_FIELD_USAGE_ID = 57  # 利用用途のカスタムフィールドID
INVALID_OCTETS = {0, 1, 255}  # 使用禁止の第4オクテット値
IPV4_PATTERN = re.compile(r'^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$')


def _redmine_url():
    return db.get_setting('redmine_url', '').strip()


def _project_name():
    return db.get_setting('project_name', '').strip()


def _default_start_octet():
    return db.get_int_setting('default_start_octet', 2)


def _default_tracker_id():
    return db.get_int_setting('default_tracker_id', 12)


def _default_status_id():
    return db.get_int_setting('default_status_id', 13)


def _default_priority_id():
    return db.get_int_setting('default_priority_id', 2)


def _get_redmine_api_key(api_key=None):
    """Redmine APIキーを返す。未設定なら例外を投げる。"""
    resolved_api_key = (api_key or '').strip() or db.get_setting('redmine_api_key', '')
    if not resolved_api_key:
        raise RuntimeError('REDMINE_API_KEY が設定されていません。')
    return resolved_api_key


def _get_headers(api_key=None):
    return {'X-Redmine-API-Key': _get_redmine_api_key(api_key)}


def get_current_user(api_key=None):
    """現在のAPIキーに紐づく Redmine ユーザー情報を返す。"""
    response = requests.get(
        f'{_redmine_url()}/users/current.json',
        headers=_get_headers(api_key),
        timeout=30,
    )
    if response.status_code != 200:
        raise RuntimeError(
            'APIキーの検証に失敗しました。'
            f' status={response.status_code}, response={response.text}'
        )
    user = response.json().get('user')
    if not isinstance(user, dict):
        raise RuntimeError('APIキーの検証結果にユーザー情報が含まれていません。')
    return user


def allocate_next_ip(project_id, subnet_prefix, reserved_octets=None, api_key=None):
    """指定サブネットで次に使えるIPアドレスと第4オクテットを返す。"""
    reserved_octets = set(reserved_octets or set())
    issues = get_issues_by_subnet(project_id, subnet_prefix, api_key=api_key)

    max_octet = get_max_octet_by_subnet(issues, subnet_prefix)
    used_octets = get_used_octets_by_subnet(issues, subnet_prefix)
    used_octets.update(reserved_octets)

    if max_octet is None:
        next_octet = _default_start_octet()
    else:
        next_octet = max_octet + 1

    while next_octet in used_octets:
        next_octet += 1

    if next_octet > 254:
        raise RuntimeError(
            f'[エラー] {subnet_prefix} の利用可能なIPが見つかりません。'
            f'第4オクテットが 254 を超えました。'
        )

    if next_octet in INVALID_OCTETS:
        raise RuntimeError(
            f'[エラー] {subnet_prefix}.{next_octet} は使用禁止の値（0/1/255）のため'
            f'チケットを作成しません。'
        )

    return f'{subnet_prefix}.{next_octet}', next_octet



def get_issues_by_subnet(project_id, subnet_prefix, api_key=None):
    """指定サブネットに一致するチケットのみをページネーション対応で取得して返す"""
    headers = _get_headers(api_key)
    issues = []
    limit = 100
    offset = 0
    while True:
        response = requests.get(
            f'{_redmine_url()}/projects/{project_id}/issues.json',
            params={
                'include': 'custom_fields',
                'limit': limit,
                'offset': offset,
                'f[]': 'cf_52',
                'op[cf_52]': '~',
                'v[cf_52][]': subnet_prefix + '.',
            },
            headers=headers
        )
        if response.status_code != 200:
            print(f'サブネット {subnet_prefix} のチケット取得に失敗しました。')
            print('ステータスコード:', response.status_code)
            print('レスポンス:', response.text)
            break
        data = response.json()
        batch = data['issues']
        issues.extend(batch)
        total = data.get('total_count', 0)
        offset += len(batch)
        if offset >= total or not batch:
            break
    return issues


def get_max_octet_by_subnet(issues, subnet_prefix):
    """custom_field_id=52 からsubnet_prefixに一致するIPの第4オクテット最大値を返す。一致なしはNone"""
    prefix = subnet_prefix + '.'
    max_octet = None
    for issue in issues:
        for field in issue.get('custom_fields', []):
            if field['id'] == CUSTOM_FIELD_IP_ID:
                value = field.get('value', '')
                if isinstance(value, str) and value.startswith(prefix):
                    parts = value.split('.')
                    if len(parts) == 4:
                        try:
                            octet = int(parts[3])
                            if max_octet is None or octet > max_octet:
                                max_octet = octet
                        except ValueError:
                            pass
    return max_octet


def get_used_octets_by_subnet(issues, subnet_prefix):
    """custom_field_id=52 からsubnet_prefixに一致する第4オクテットの使用済み集合を返す"""
    prefix = subnet_prefix + '.'
    used_octets = set()
    for issue in issues:
        for field in issue.get('custom_fields', []):
            if field['id'] == CUSTOM_FIELD_IP_ID:
                value = field.get('value', '')
                if isinstance(value, str) and value.startswith(prefix):
                    parts = value.split('.')
                    if len(parts) == 4:
                        try:
                            used_octets.add(int(parts[3]))
                        except ValueError:
                            pass
    return used_octets


def create_redmine_ticket(project_id, ticket_data, ip_address, api_key=None):
    """チケットを登録してcustom_field_id=52にip_addressをセットする。結果dictを返す"""
    headers = {
        'X-Redmine-API-Key': _get_redmine_api_key(api_key),
        'Content-Type': 'application/json'
    }
    custom_fields = list(ticket_data.get('custom_fields', []))
    # IPアドレスフィールドを追加（既存の同IDエントリは上書き）
    custom_fields = [f for f in custom_fields if f.get('id') != CUSTOM_FIELD_IP_ID]
    custom_fields.append({'id': CUSTOM_FIELD_IP_ID, 'value': ip_address})

    payload = {
        'issue': {
            'project_id': project_id,
            'subject': ticket_data['subject'],
            'description': ticket_data.get('description', ''),
            'tracker_id': ticket_data['tracker_id'],
            'priority_id': ticket_data['priority_id'],
            'custom_fields': custom_fields,
        }
    }
    # オプションフィールド（存在且つNoneでない場合のみ追加）
    if ticket_data.get('status_id') is not None:
        payload['issue']['status_id'] = ticket_data['status_id']
    if ticket_data.get('assigned_to_id') is not None:
        payload['issue']['assigned_to_id'] = ticket_data['assigned_to_id']
    if ticket_data.get('start_date') is not None:
        payload['issue']['start_date'] = ticket_data['start_date']
    if ticket_data.get('due_date') is not None:
        payload['issue']['due_date'] = ticket_data['due_date']
    
    response = requests.post(
        f'{_redmine_url()}/issues.json',
        data=json.dumps(payload),
        headers=headers
    )
    if response.status_code in (200, 201):
        created = response.json()['issue']
        return {
            'result': 'success',
            'id': created['id'],
            'ip': ip_address,
            'url': f"{_redmine_url()}/issues/{created['id']}",
        }
    else:
        return {
            'result': 'error',
            'ip': ip_address,
            'status_code': response.status_code,
            'message': response.text
        }


def create_tickets():
    """旧テンプレート登録フローは廃止。フォーム/API駆動のみサポートする。"""
    raise RuntimeError('API依存モードでは create_tickets は使用できません。フォームから登録してください。')

# RedmineのプロジェクトIDを取得するコード
def get_redmine_project_id(project_name, api_key=None):
    headers = _get_headers(api_key)
    response = requests.get(f'{_redmine_url()}/projects.json', headers=headers)
    if response.status_code == 200:
        projects = response.json()['projects']
        for project in projects:
            if project['name'] == project_name:
                return project['id']
        print(f'プロジェクト "{project_name}" が見つかりませんでした。')
        return None
    else:
        print('プロジェクトの取得に失敗しました。')
        print('ステータスコード:', response.status_code)
        print('レスポンス:', response.text)
        return None


def get_project_assignee_map(project_id, api_key=None):
    """プロジェクトのメンバーから 担当者名 -> ユーザーID の対応表を返す。"""
    headers = _get_headers(api_key)
    limit = 100
    offset = 0
    assignees = {}

    while True:
        response = requests.get(
            f'{_redmine_url()}/projects/{project_id}/memberships.json',
            params={'limit': limit, 'offset': offset},
            headers=headers,
            timeout=30,
        )
        if response.status_code != 200:
            raise RuntimeError(
                '担当者情報の取得に失敗しました。'
                f' status={response.status_code}, response={response.text}'
            )

        data = response.json()
        memberships = data.get('memberships', [])
        for membership in memberships:
            user = membership.get('user')
            if not user:
                continue
            user_name = user.get('name')
            user_id = user.get('id')
            if isinstance(user_name, str):
                user_name = user_name.strip()
            if user_name and user_id:
                assignees[user_name] = user_id

        total = data.get('total_count', 0)
        offset += len(memberships)
        if not memberships or offset >= total:
            break

    return assignees


def get_vhost_display_map_from_redmine(field_id=CUSTOM_FIELD_VHOST_IP_ID, api_key=None):
    """カスタムフィールドの候補値から 内部値 -> 表示値 の対応表を返す。"""
    headers = _get_headers(api_key)
    response = requests.get(
        f'{_redmine_url()}/custom_fields/{field_id}.json',
        headers=headers,
        timeout=30,
    )
    if response.status_code != 200:
        raise RuntimeError(
            '仮想ホスト候補の取得に失敗しました。'
            f' status={response.status_code}, response={response.text}'
        )

    data = response.json()
    custom_field = data.get('custom_field', {})
    possible_values = custom_field.get('possible_values', [])

    display_map = {}
    for item in possible_values:
        if isinstance(item, dict):
            raw_value = item.get('value')
            label = item.get('label', raw_value)
        else:
            raw_value = item
            label = item

        if raw_value is None:
            continue
        display_map[str(raw_value)] = '' if label is None else str(label)

    return display_map


def _normalize_option_sort_key(option):
    value, label = option
    return (str(label), str(value))


def _extract_option_values_from_issues(project_id, field_id, api_key=None):
    """既存チケットのcustom_fieldsから候補値を抽出し [(value, label)] で返す。"""
    headers = _get_headers(api_key)
    limit = 100
    offset = 0
    values = set()

    while True:
        response = requests.get(
            f'{_redmine_url()}/projects/{project_id}/issues.json',
            params={'include': 'custom_fields', 'limit': limit, 'offset': offset},
            headers=headers,
            timeout=30,
        )
        if response.status_code != 200:
            raise RuntimeError(
                f'チケットから候補抽出に失敗しました (field_id={field_id})。'
                f' status={response.status_code}, response={response.text}'
            )

        data = response.json()
        issues = data.get('issues', [])
        total = data.get('total_count', 0)

        for issue in issues:
            for custom_field in issue.get('custom_fields', []):
                if custom_field.get('id') != field_id:
                    continue
                field_value = custom_field.get('value')
                if isinstance(field_value, list):
                    candidates = field_value
                else:
                    candidates = [field_value]
                for candidate in candidates:
                    if candidate is None:
                        continue
                    text = str(candidate).strip()
                    if text:
                        values.add(text)

        offset += len(issues)
        if not issues or offset >= total:
            break

    options = [(value, value) for value in values]
    return sorted(options, key=_normalize_option_sort_key)


def _decode_csv_content(csv_bytes):
    for encoding in ('utf-8-sig', 'cp932', 'shift_jis', 'utf-8'):
        try:
            return csv_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    return csv_bytes.decode('utf-8', errors='replace')


def _split_csv_multi_values(cell_text):
    text = (cell_text or '').strip()
    if not text:
        return []
    if ',' not in text:
        return [text]
    return [item.strip() for item in text.split(',') if item.strip()]


def _fetch_issue_custom_field_labels_from_csv(project_id, field_ids, api_key=None):
    """issues.csv から issue_id -> {field_id: [label, ...]} を返す。"""
    headers = _get_headers(api_key)
    params = {
        'project_id': str(project_id),
        'set_filter': '1',
        'status_id': '*',
        'limit': '10000',
        'c[]': ['id'] + [f'cf_{field_id}' for field_id in field_ids],
    }
    response = requests.get(
        f'{_redmine_url()}/issues.csv',
        params=params,
        headers=headers,
        timeout=60,
    )
    if response.status_code != 200:
        raise RuntimeError(
            'CSVから候補ラベルの取得に失敗しました。'
            f' status={response.status_code}, response={response.text[:200]}'
        )

    decoded = _decode_csv_content(response.content)
    reader = csv.reader(io.StringIO(decoded))
    rows = list(reader)
    if not rows:
        return {}

    labels_by_issue = {}
    for row in rows[1:]:
        if not row:
            continue
        issue_id_text = (row[0] or '').strip()
        if not issue_id_text:
            continue
        try:
            issue_id = int(issue_id_text)
        except ValueError:
            continue

        field_map = {}
        for index, field_id in enumerate(field_ids, start=1):
            cell = row[index] if index < len(row) else ''
            field_map[field_id] = _split_csv_multi_values(cell)
        labels_by_issue[issue_id] = field_map

    return labels_by_issue


def _fetch_issue_custom_field_ids_from_issues(project_id, field_ids, api_key=None):
    """issues.json から issue_id -> {field_id: [id, ...]} を返す。"""
    headers = _get_headers(api_key)
    limit = 100
    offset = 0
    ids_by_issue = {}

    while True:
        response = requests.get(
            f'{_redmine_url()}/projects/{project_id}/issues.json',
            params={'include': 'custom_fields', 'limit': limit, 'offset': offset},
            headers=headers,
            timeout=30,
        )
        if response.status_code != 200:
            raise RuntimeError(
                'チケットから候補IDの取得に失敗しました。'
                f' status={response.status_code}, response={response.text}'
            )

        data = response.json()
        issues = data.get('issues', [])
        total = data.get('total_count', 0)

        for issue in issues:
            issue_id = issue.get('id')
            if not issue_id:
                continue
            field_map = {}
            for custom_field in issue.get('custom_fields', []):
                field_id = custom_field.get('id')
                if field_id not in field_ids:
                    continue
                raw_value = custom_field.get('value')
                if isinstance(raw_value, list):
                    values = [str(item).strip() for item in raw_value if str(item).strip()]
                else:
                    text = '' if raw_value is None else str(raw_value).strip()
                    values = [text] if text else []
                field_map[field_id] = values
            ids_by_issue[issue_id] = field_map

        offset += len(issues)
        if not issues or offset >= total:
            break

    return ids_by_issue


def _build_field_label_map(project_id, field_id, api_key=None):
    """issues.json のID値と issues.csv の表示値から ID->表示値 の対応を推定する。"""
    ids_by_issue = _fetch_issue_custom_field_ids_from_issues(project_id, [field_id], api_key=api_key)
    labels_by_issue = _fetch_issue_custom_field_labels_from_csv(project_id, [field_id], api_key=api_key)

    candidates = {}
    direct_map = {}

    for issue_id, issue_fields in ids_by_issue.items():
        id_values = issue_fields.get(field_id, [])
        label_values = labels_by_issue.get(issue_id, {}).get(field_id, [])
        if not id_values or not label_values:
            continue

        id_set = {value for value in id_values if value}
        label_set = {value for value in label_values if value}
        if not id_set or not label_set:
            continue

        if len(id_set) == 1 and len(label_set) == 1:
            direct_map[next(iter(id_set))] = next(iter(label_set))

        for id_value in id_set:
            if id_value in direct_map:
                continue
            if id_value in candidates:
                candidates[id_value] &= label_set
            else:
                candidates[id_value] = set(label_set)

    resolved = dict(direct_map)
    changed = True
    while changed:
        changed = False
        fixed_labels = set(resolved.values())
        for id_value, candidate_labels in candidates.items():
            if id_value in resolved:
                continue
            candidate_labels -= fixed_labels
            if len(candidate_labels) == 1:
                resolved[id_value] = next(iter(candidate_labels))
                changed = True

    return resolved


def _build_field_label_maps(project_id, field_ids, api_key=None):
    """複数フィールドの ID->表示値 を一括推定して返す。"""
    ids_by_issue = _fetch_issue_custom_field_ids_from_issues(project_id, field_ids, api_key=api_key)
    labels_by_issue = _fetch_issue_custom_field_labels_from_csv(project_id, field_ids, api_key=api_key)

    result = {}
    for field_id in field_ids:
        candidates = {}
        direct_map = {}

        for issue_id, issue_fields in ids_by_issue.items():
            id_values = issue_fields.get(field_id, [])
            label_values = labels_by_issue.get(issue_id, {}).get(field_id, [])
            if not id_values or not label_values:
                continue

            id_set = {value for value in id_values if value}
            label_set = {value for value in label_values if value}
            if not id_set or not label_set:
                continue

            if len(id_set) == 1 and len(label_set) == 1:
                direct_map[next(iter(id_set))] = next(iter(label_set))

            for id_value in id_set:
                if id_value in direct_map:
                    continue
                if id_value in candidates:
                    candidates[id_value] &= label_set
                else:
                    candidates[id_value] = set(label_set)

        resolved = dict(direct_map)
        changed = True
        while changed:
            changed = False
            fixed_labels = set(resolved.values())
            for id_value, candidate_labels in candidates.items():
                if id_value in resolved:
                    continue
                candidate_labels -= fixed_labels
                if len(candidate_labels) == 1:
                    resolved[id_value] = next(iter(candidate_labels))
                    changed = True

        result[field_id] = resolved

    return result


def get_custom_field_options_batch_from_redmine(field_ids, api_key=None, project_id=None):
    """複数カスタムフィールド候補を一括で取得して返す。戻り値は {field_id: [(value, label), ...]}。"""
    resolved_project_id = project_id
    if resolved_project_id is None:
        resolved_project_id = get_redmine_project_id(_project_name(), api_key=api_key)
    if resolved_project_id is None:
        raise RuntimeError('プロジェクトIDが取得できません。')

    ids_by_issue = _fetch_issue_custom_field_ids_from_issues(resolved_project_id, field_ids, api_key=api_key)
    label_maps = _build_field_label_maps(resolved_project_id, field_ids, api_key=api_key)

    options_by_field = {}
    for field_id in field_ids:
        id_values = set()
        for issue_fields in ids_by_issue.values():
            for id_value in issue_fields.get(field_id, []):
                if id_value:
                    id_values.add(id_value)

        label_map = label_maps.get(field_id, {})
        options = [(id_value, label_map.get(id_value, id_value)) for id_value in sorted(id_values)]
        options_by_field[field_id] = sorted(options, key=_normalize_option_sort_key)

    return options_by_field


def get_custom_field_options_from_redmine(field_id, api_key=None, project_id=None):
    """カスタムフィールドの候補値を [(value, label), ...] で返す。"""
    headers = _get_headers(api_key)
    response = requests.get(
        f'{_redmine_url()}/custom_fields/{field_id}.json',
        headers=headers,
        timeout=30,
    )
    if response.status_code != 200:
        if response.status_code == 404:
            resolved_project_id = project_id
            if resolved_project_id is None:
                resolved_project_id = get_redmine_project_id(_project_name(), api_key=api_key)
            if resolved_project_id is None:
                raise RuntimeError(
                    f'カスタムフィールド候補の取得に失敗しました (field_id={field_id})。'
                    'プロジェクトIDが取得できません。'
                )
            raw_options = _extract_option_values_from_issues(resolved_project_id, field_id, api_key=api_key)
            label_map = _build_field_label_map(resolved_project_id, field_id, api_key=api_key)
            options = []
            for id_value, _ in raw_options:
                options.append((id_value, label_map.get(id_value, id_value)))
            return sorted(options, key=_normalize_option_sort_key)
        raise RuntimeError(
            f'カスタムフィールド候補の取得に失敗しました (field_id={field_id})。'
            f' status={response.status_code}, response={response.text}'
        )

    data = response.json()
    custom_field = data.get('custom_field', {})
    possible_values = custom_field.get('possible_values', [])

    options = []
    for item in possible_values:
        if isinstance(item, dict):
            raw_value = item.get('value')
            label = item.get('label', raw_value)
        else:
            raw_value = item
            label = item

        if raw_value is None:
            continue
        value_text = str(raw_value)
        label_text = '' if label is None else str(label)
        options.append((value_text, label_text))

    return sorted(options, key=_normalize_option_sort_key)


def update_master_options(os_options, usage_options, updated_by='system'):
    """SQLite のマスタ候補（OS/利用用途）を更新する。"""
    db.replace_master_options('os', os_options, updated_by=updated_by)
    db.replace_master_options('usage', usage_options, updated_by=updated_by)


def _is_valid_ipv4(ip_text):
    match = IPV4_PATTERN.match((ip_text or '').strip())
    if not match:
        return False
    octets = [int(group) for group in match.groups()]
    return all(0 <= value <= 255 for value in octets)


def _extract_subnet_prefix(ip_text):
    if not _is_valid_ipv4(ip_text):
        return None
    parts = ip_text.strip().split('.')
    return '.'.join(parts[:3])


def is_ip_already_registered(project_id, ip_address, api_key=None):
    """指定したIPが custom_field_id=52 に既に登録済みかを返す。"""
    normalized_ip = (ip_address or '').strip()
    subnet_prefix = _extract_subnet_prefix(normalized_ip)
    if subnet_prefix is None:
        raise RuntimeError(f'IPアドレス形式が不正です: {ip_address}')

    issues = get_issues_by_subnet(project_id, subnet_prefix, api_key=api_key)
    for issue in issues:
        for field in issue.get('custom_fields', []):
            if field.get('id') != CUSTOM_FIELD_IP_ID:
                continue
            value = field.get('value', '')
            if isinstance(value, list):
                candidates = value
            else:
                candidates = [value]
            for candidate in candidates:
                if isinstance(candidate, str) and candidate.strip() == normalized_ip:
                    return True
    return False


def get_subnet_prefixes_from_redmine(project_id, field_id=CUSTOM_FIELD_IP_ID, api_key=None):
    """既存チケットのIPカスタムフィールドから /24 サブネット候補を抽出して返す。"""
    headers = _get_headers(api_key)
    issues = []
    limit = 100
    offset = 0

    while True:
        response = requests.get(
            f'{_redmine_url()}/projects/{project_id}/issues.json',
            params={'include': 'custom_fields', 'limit': limit, 'offset': offset},
            headers=headers,
            timeout=30,
        )
        if response.status_code != 200:
            raise RuntimeError(
                'サブネット候補の取得に失敗しました。'
                f' status={response.status_code}, response={response.text}'
            )

        data = response.json()
        batch = data.get('issues', [])
        issues.extend(batch)
        total = data.get('total_count', 0)
        offset += len(batch)
        if not batch or offset >= total:
            break

    prefixes = set()
    for issue in issues:
        for custom_field in issue.get('custom_fields', []):
            if custom_field.get('id') != field_id:
                continue
            field_value = custom_field.get('value')
            if isinstance(field_value, list):
                values = field_value
            else:
                values = [field_value]
            for value in values:
                prefix = _extract_subnet_prefix('' if value is None else str(value))
                if prefix:
                    prefixes.add(prefix)

    def _sort_key(prefix):
        return tuple(int(part) for part in prefix.split('.'))

    return sorted(prefixes, key=_sort_key)


def _get_default_subnet_prefixes_from_db():
    prefixes = [value for value, _ in db.get_master_options('subnet') if value.strip()]

    def _sort_key(prefix):
        return tuple(int(part) for part in prefix.split('.'))

    return sorted(set(prefixes), key=_sort_key)


def fetch_latest_form_master(project_name, api_key=None):
    """フォームに必要な最新マスタ情報をRedmineから取得して返す。"""
    project_id = get_redmine_project_id(project_name, api_key=api_key)
    if project_id is None:
        raise RuntimeError(f'プロジェクトが見つかりません: {project_name}')

    warnings = []

    assignee_map = dict(db.get_assignee_name_to_id())
    try:
        latest_assignee_map = get_project_assignee_map(project_id, api_key=api_key)
        if latest_assignee_map:
            assignee_map = latest_assignee_map
    except Exception as exc:
        warnings.append(f'担当者情報は既存設定を利用: {exc}')

    vhost_display_map = dict(db.get_vhost_ip_display_map())
    try:
        latest_vhost_map = get_vhost_display_map_from_redmine(api_key=api_key)
        if latest_vhost_map:
            vhost_display_map = latest_vhost_map
    except Exception as exc:
        warnings.append(f'仮想ホスト候補は既存設定を利用: {exc}')

    subnet_prefixes = _get_default_subnet_prefixes_from_db()
    try:
        latest_subnet_prefixes = get_subnet_prefixes_from_redmine(project_id, api_key=api_key)
        if latest_subnet_prefixes:
            subnet_prefixes = latest_subnet_prefixes
    except Exception as exc:
        warnings.append(f'サブネット候補は既存設定を利用: {exc}')

    os_options = list(db.get_master_options('os'))
    usage_options = list(db.get_master_options('usage'))
    try:
        latest_options = get_custom_field_options_batch_from_redmine(
            [CUSTOM_FIELD_OS_ID, CUSTOM_FIELD_USAGE_ID],
            api_key=api_key,
            project_id=project_id,
        )
        latest_os_options = latest_options.get(CUSTOM_FIELD_OS_ID, [])
        latest_usage_options = latest_options.get(CUSTOM_FIELD_USAGE_ID, [])
        if latest_os_options:
            os_options = latest_os_options
        if latest_usage_options:
            usage_options = latest_usage_options
    except Exception as exc:
        warnings.append(f'OS/利用用途候補は既存設定を利用: {exc}')

    return {
        'assignee_name_to_id': assignee_map,
        'vhost_ip_display_map': vhost_display_map,
        'subnet_prefixes': subnet_prefixes,
        'os_options': os_options,
        'usage_options': usage_options,
        'warnings': warnings,
    }
    
    