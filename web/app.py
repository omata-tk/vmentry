import os

from flask import Flask, redirect, render_template, request, session, url_for

from core import db
from services import hyperv, redmine


app = Flask(__name__, template_folder='../templates')
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'ticket-maker-dev-secret-change-me')
db.init_db()
db.assert_runtime_db_ready()

SESSION_API_KEY = 'redmine_api_key'
SESSION_USER_NAME = 'redmine_user_name'
SESSION_USER_ROLE = 'user_role'
SESSION_IS_ADMIN = 'is_admin'

CURRENT_ASSIGNEE_NAME_TO_ID = dict(db.get_assignee_name_to_id())
CURRENT_VHOST_IP_DISPLAY_MAP = dict(db.get_vhost_ip_display_map())

DEFAULT_VM_TEMPLATE_OPTIONS = list(db.get_master_options('vm_template'))
CURRENT_VM_TEMPLATE_OPTIONS = list(DEFAULT_VM_TEMPLATE_OPTIONS)

DEFAULT_SUBNET_OPTIONS = list(db.get_master_options('subnet'))
CURRENT_SUBNET_OPTIONS = list(DEFAULT_SUBNET_OPTIONS)
DEFAULT_OS_OPTIONS = list(db.get_master_options('os'))
CURRENT_OS_OPTIONS = list(DEFAULT_OS_OPTIONS)

DEFAULT_USAGE_OPTIONS = list(db.get_master_options('usage'))
CURRENT_USAGE_OPTIONS = list(DEFAULT_USAGE_OPTIONS)

def _assignee_id_to_name(assignee_id):
	if assignee_id is None:
		return ''
	for name, mapped_id in CURRENT_ASSIGNEE_NAME_TO_ID.items():
		if mapped_id == assignee_id:
			return name
	return ''


def _form_defaults():
	target_default = CURRENT_SUBNET_OPTIONS[0][0] if CURRENT_SUBNET_OPTIONS else ''
	return {
		'target_subnet': target_default,
		'subject': db.get_setting('form_default_subject', ''),
		'assignee_name': '',
		'start_date': '',
		'due_date': '',
		'description': '',
		'vm_name': '',
		'os_value': '',
		'os_product_key': '',
		'os_product_key_owner': '',
		'vhost_ip': '',
		'os_user': db.get_setting('form_default_os_user', ''),
		'os_password': '',
		'usage': '',
		'usage_other': '',
		'system_login_info': '',
		'initial_builder': '',
		'notes': '',
		'deploy_type': 'template',   # template or manual
		'vm_template': '',
		'memory': '',
		'disk': '',
		'cpu': '',
		'switch': '',
	}

CONFIRM_FIELDS = [
	('target_subnet', '対象サブネット'),
	('subject', 'Redmineチケット名'),
	('assignee_name', '担当者'),
	('vm_name', '仮想マシン名'),
	('start_date', '開始日'),
	('due_date', '期日'),
	('description', '説明文'),
	('os_value', 'OS'),
	('vhost_ip', 'IPアドレス（仮想ホスト）'),
	('os_user', 'OSユーザー'),
	('os_password', 'OSパスワード'),
	('os_product_key', 'OSプロダクトキー'),
	('os_product_key_owner', 'OSプロダクトキー所有者'),
	('usage', '利用用途'),
	('usage_other', '利用用途（その他）'),
	('system_login_info', 'システムログイン情報'),
	('initial_builder', '初期構築担当者'),
	('notes', '特記事項'),
	("vm_template", "VMテンプレート"),
	("memory", "メモリ(GB)"),
	("disk", "ディスク(GB)"),
	("cpu", "CPU数"),
	("switch", "仮想スイッチ"),
]


def _build_values(form_data):
	form_defaults = _form_defaults()
	values = {}
	for key, default in form_defaults.items():
		if key == 'usage' and hasattr(form_data, 'getlist'):
			checked_values = [item.strip() for item in form_data.getlist('usage') if isinstance(item, str) and item.strip()]
			if checked_values:
				values[key] = ', '.join(checked_values)
				continue
		values[key] = form_data.get(key, default)
	if not values.get('target_subnet') and CURRENT_SUBNET_OPTIONS:
		values['target_subnet'] = CURRENT_SUBNET_OPTIONS[0][0]
	
	reverse_map = {v: k for k, v in CURRENT_VHOST_IP_DISPLAY_MAP.items()}
	v = values.get('vhost_ip')
	if v in reverse_map:
		values['vhost_ip'] = reverse_map[v]

	return values


def _build_confirm_display_values(values):
	display_values = dict(values)
	os_label_map = {value: label for value, label in CURRENT_OS_OPTIONS}
	vhost_label_map = dict(CURRENT_VHOST_IP_DISPLAY_MAP)
	display_values['os_value'] = os_label_map.get(values.get('os_value', ''), values.get('os_value', ''))
	display_values['vhost_ip'] = vhost_label_map.get(values.get('vhost_ip', ''), values.get('vhost_ip', ''))
	return display_values


def _refresh_master_data_from_redmine(api_key, executed_by=None):
	executor_name = (executed_by or '').strip() or _get_session_user_name()
	latest = redmine.fetch_latest_form_master(db.get_setting('project_name', ''), api_key=api_key)
	CURRENT_ASSIGNEE_NAME_TO_ID.clear()
	CURRENT_ASSIGNEE_NAME_TO_ID.update(latest.get('assignee_name_to_id', {}))
	CURRENT_VHOST_IP_DISPLAY_MAP.clear()
	CURRENT_VHOST_IP_DISPLAY_MAP.update(latest.get('vhost_ip_display_map', {}))
	subnet_prefixes = latest.get('subnet_prefixes', [])
	CURRENT_SUBNET_OPTIONS.clear()
	if subnet_prefixes:
		for subnet_prefix in subnet_prefixes:
			CURRENT_SUBNET_OPTIONS.append((subnet_prefix, subnet_prefix))
	else:
		CURRENT_SUBNET_OPTIONS.extend(DEFAULT_SUBNET_OPTIONS)
	latest_os_options = latest.get('os_options', [])
	CURRENT_OS_OPTIONS.clear()
	if latest_os_options:
		for value, label in latest_os_options:
			CURRENT_OS_OPTIONS.append((value, label))
	else:
		CURRENT_OS_OPTIONS.extend(DEFAULT_OS_OPTIONS)
	latest_usage_options = latest.get('usage_options', [])
	CURRENT_USAGE_OPTIONS.clear()
	if latest_usage_options:
		for value, label in latest_usage_options:
			CURRENT_USAGE_OPTIONS.append((value, label))
	else:
		CURRENT_USAGE_OPTIONS.extend(DEFAULT_USAGE_OPTIONS)

	config_sync_error = None
	try:
		redmine.update_master_options(CURRENT_OS_OPTIONS, CURRENT_USAGE_OPTIONS, updated_by=executor_name)
		db.replace_master_options(
			'subnet',
			[(item[0], item[1]) for item in CURRENT_SUBNET_OPTIONS],
			updated_by=executor_name,
		)
		db.replace_master_options(
			'assignee',
			[(str(user_id), name) for name, user_id in CURRENT_ASSIGNEE_NAME_TO_ID.items()],
			updated_by=executor_name,
		)
		db.replace_master_options(
			'vhost',
			[(value, label) for value, label in CURRENT_VHOST_IP_DISPLAY_MAP.items()],
			updated_by=executor_name,
		)
  
		# Hyper-V仮想マシンテンプレートを取得
		vm_templates = hyperv.get_vm_templates() 
		# DBに保存
		db.replace_master_options(
			'vm_template',
			vm_templates,
			updated_by=executor_name,
		)
	except Exception as exc:
		config_sync_error = str(exc)
		db.append_log('sync', executor_name, 'error', config_sync_error)
	else:
		db.append_log('sync', executor_name, 'info', '最新情報取得に成功しました。')
	notice_message = (
		f'担当者 {len(CURRENT_ASSIGNEE_NAME_TO_ID)} 件、'
		f'仮想ホスト候補 {len(CURRENT_VHOST_IP_DISPLAY_MAP)} 件、'
		f'サブネット候補 {len(CURRENT_SUBNET_OPTIONS)} 件、'
		f'OS候補 {len(CURRENT_OS_OPTIONS)} 件、'
		f'利用用途候補 {len(CURRENT_USAGE_OPTIONS)} 件を反映しました。'
	)
	if config_sync_error:
		notice_message += f' SQLite 更新は失敗: {config_sync_error}'
	else:
		notice_message += ' SQLite へ候補を保存しました。'
	warnings = [item for item in latest.get('warnings', []) if isinstance(item, str) and item.strip()]
	if warnings:
		notice_message += ' 一部項目は取得不可のため既存設定を使用しました。'
	return notice_message

def _build_hyperv_hosts_from_settings():
	settings = db.get_all_settings()
	hosts = []
	for i in range(1, 4):
		hosts.append(
			{
			"ip": (settings.get(f"hyperv_host{i}_ip") or "").strip(),
			"user": (settings.get(f"hyperv_host{i}_user") or "").strip(),
			"password": (settings.get(f"hyperv_host{i}_password") or "").strip(),
			}
		)
	return hosts

def _refresh_vm_templates(executed_by=None):
	executor_name = (executed_by or '').strip() or _get_session_user_name()
	try:
		hosts = _build_hyperv_hosts_from_settings()
		vm_templates, host_results = hyperv.get_vm_templates_from_hosts(hosts)

		# ホストごとの結果をログ化
		for result in host_results:
			status = result.get("status", "info")
			msg = result.get("message", "")
			if status in ("success", "skipped"):
				db.append_log('sync', executor_name, 'info', msg)
			else:
				db.append_log('sync', executor_name, 'error', msg)

		CURRENT_VM_TEMPLATE_OPTIONS.clear()
		if vm_templates:
			CURRENT_VM_TEMPLATE_OPTIONS.extend(vm_templates)
			db.replace_master_options(
				"vm_template",
				vm_templates,
				updated_by=executor_name,
			)
			return f"VMテンプレート {len(vm_templates)} 件を反映しました。"

		# 1件も取れない場合は既定値へフォールバック
		CURRENT_VM_TEMPLATE_OPTIONS.extend(DEFAULT_VM_TEMPLATE_OPTIONS)
		return "VMテンプレートを取得できませんでした。既存設定を使用します。"

	except Exception as exc:
		db.append_log('sync', executor_name, 'error', f"Hyper-V取得失敗: {str(exc)}")
		raise


def _get_session_api_key():
	return (session.get(SESSION_API_KEY) or '').strip()


def _get_session_user_name():
	return (session.get(SESSION_USER_NAME) or '').strip() or 'Unknown User'


def _is_admin_session():
	return bool(session.get(SESSION_IS_ADMIN))


def _build_user_display_name(user):
	first_name = (user.get('firstname') or '').strip()
	last_name = (user.get('lastname') or '').strip()
	full_name = f'{last_name} {first_name}'.strip()
	if full_name:
		return full_name
	return (user.get('login') or '').strip() or 'Unknown User'


def _build_ticket_data(form_data):
    errors = []
    selected_subnets = {value for value, _ in CURRENT_SUBNET_OPTIONS}
    
    # デバッグモードの確認
    debug_ticket_only = (form_data.get('debug_ticket_only') or '').strip() == 'on'

    target_subnet = (form_data.get('target_subnet') or '').strip()
    subject = (form_data.get('subject') or '').strip()
    vm_name = (form_data.get('vm_name') or '').strip()
    if not target_subnet:
        errors.append('対象サブネットを選択してください。')
    elif selected_subnets and target_subnet not in selected_subnets:
        errors.append('対象サブネットは候補から選択してください。')
    if not subject:
        errors.append('Redmineチケット名は必須です。')
    
    # VM関連の検証はデバッグモードではスキップ
    if not debug_ticket_only:
        if not vm_name:
            errors.append('仮想マシン名は必須です。')

    deploy_type = (form_data.get('deploy_type') or '').strip()
    vm_template = (form_data.get('vm_template') or '').strip()

    memory = (form_data.get('memory') or '').strip()
    disk   = (form_data.get('disk') or '').strip()
    cpu	= (form_data.get('cpu') or '').strip()

    if deploy_type and deploy_type not in ('template', 'manual'):
       	errors.append('作成方法が不正です。')

    # VM関連フィールドの検証はデバッグモードではスキップ
    if not debug_ticket_only:
        if deploy_type == 'template':
            if not vm_template:
                errors.append('VMテンプレートを選択してください。')
        elif deploy_type == 'manual':
            if not memory or not disk or not cpu:
                errors.append('手動作成の場合はメモリ・ディスク・CPUが必須です。')
            else:
                try:
                    int(memory)
                    int(disk)
                    int(cpu)
                except ValueError:
                    errors.append('メモリ・ディスク・CPUは数値で入力してください。')

    assignee_name = (form_data.get('assignee_name') or '').strip()
    assigned_to_id = None
    if assignee_name:
        if assignee_name not in CURRENT_ASSIGNEE_NAME_TO_ID:
            errors.append('担当者は登録済みの名前を入力してください。')
        else:
            assigned_to_id = CURRENT_ASSIGNEE_NAME_TO_ID[assignee_name]
    usage_values = []
    if hasattr(form_data, 'getlist'):
        usage_values = [item.strip() for item in form_data.getlist('usage') if isinstance(item, str) and item.strip()]
    if not usage_values:
        usage_raw = (form_data.get('usage') or '').strip()
        usage_values = [item.strip() for item in usage_raw.split(',') if item.strip()]

    ticket_data = {
        'target_subnet': target_subnet,
        'subject': subject,
        'tracker_id': db.get_int_setting('default_tracker_id', 12),
        'status_id': db.get_int_setting('default_status_id', 13),
        'priority_id': db.get_int_setting('default_priority_id', 2),
        'assigned_to_id': assigned_to_id,
        'start_date': (form_data.get('start_date') or '').strip() or None,
        'due_date': (form_data.get('due_date') or '').strip() or None,
        'description': (form_data.get('description') or '').strip(),
        'custom_fields': [
            {'id': 45, 'value': vm_name},
            {'id': 43, 'value': (form_data.get('os_value') or '').strip()},
            {'id': 46, 'value': (form_data.get('os_product_key') or '').strip()},
            {'id': 50, 'value': (form_data.get('os_product_key_owner') or '').strip()},
            {'id': 53, 'value': (form_data.get('vhost_ip') or '').strip()},
            {'id': 55, 'value': (form_data.get('os_user') or '').strip()},
            {'id': 56, 'value': (form_data.get('os_password') or '').strip()},
            {'id': 57, 'value': usage_values},
            {'id': 58, 'value': (form_data.get('usage_other') or '').strip()},
            {'id': 59, 'value': (form_data.get('system_login_info') or '').strip()},
            {'id': 98, 'value': (form_data.get('initial_builder') or '').strip()},
            {'id': 35, 'value': (form_data.get('notes') or '').strip()},
        ],
        'debug_ticket_only': debug_ticket_only,
    }
    return ticket_data, errors


def _build_ticket_url(ticket_id):
	if not ticket_id:
		return None
	base = (db.get_setting('redmine_url', '') or '').rstrip('/')
	if not base:
		return None
	return f'{base}/issues/{ticket_id}'


@app.route('/login', methods=['GET', 'POST'])
def login():
	if _get_session_api_key() or _is_admin_session():
		return redirect(url_for('index'))

	error = None
	if request.method == 'POST':
		api_key = (request.form.get('api_key') or '').strip()
		if not api_key:
			error = 'APIキーを入力してください。'
		elif db.is_admin_key(api_key) or (api_key and api_key == (db.get_setting('admin_username', '') or '').strip()):
			session[SESSION_API_KEY] = ''
			session[SESSION_USER_NAME] = db.get_setting('admin_username', '')
			session[SESSION_USER_ROLE] = 'admin'
			session[SESSION_IS_ADMIN] = True
			return redirect(url_for('admin'))
		else:
			try:
				user = redmine.get_current_user(api_key=api_key)
				session[SESSION_API_KEY] = api_key
				session[SESSION_USER_NAME] = _build_user_display_name(user)
				session[SESSION_USER_ROLE] = 'user'
				session[SESSION_IS_ADMIN] = False
				return redirect(url_for('index'))
			except Exception as exc:
				error = str(exc)

	return render_template('login.html', error=error)


@app.route('/logout', methods=['GET'])
def logout():
	session.clear()
	return redirect(url_for('login'))


@app.route('/admin', methods=['GET', 'POST'])
def admin():
	if not _is_admin_session():
		return redirect(url_for('login'))

	error = None
	message = None
	action = (request.form.get('action') or '').strip() if request.method == 'POST' else ''

	if request.method == 'POST' and action == 'save_settings':
		try:
			keys = [
				'redmine_url',
				'project_name',
				'default_tracker_id',
				'default_status_id',
				'default_priority_id',
				'default_start_octet',
				'form_default_subject',
				'form_default_os_user',
			]
			payload = {key: (request.form.get(key) or '').strip() for key in keys}
			db.set_settings(payload, updated_by=_get_session_user_name())
			new_key = (request.form.get('admin_magic_key') or '').strip()
			if new_key:
				db.set_admin_key(new_key)
			message = '設定を保存しました。'
		except Exception as exc:
			error = str(exc)

	if request.method == "POST" and action == "save_hyperv_hosts":
		try:
			keys = []
			for i in range(1, 4):
				keys.extend(
					[
					f"hyperv_host{i}_ip",
					f"hyperv_host{i}_user",
					f"hyperv_host{i}_password",
					]
				)
			payload = {key: (request.form.get(key) or "").strip() for key in keys}
			db.set_settings(payload, updated_by=_get_session_user_name())
			message = "Hyper-Vホスト設定を保存しました。"
		except Exception as exc:
			error = str(exc)

	if request.method == 'POST' and action == 'refresh_vm_templates':
		executor_name = _get_session_user_name()
		try:
			message = _refresh_vm_templates(executed_by=executor_name)
		except Exception as exc:
			error = str(exc)

	return render_template(
		'admin.html',
		settings=db.get_all_settings(),
		logs=db.get_recent_logs(20),
		message=message,
		error=error,
	)


@app.route('/', methods=['GET', 'POST'])
def index():
	if not (_get_session_api_key() or _is_admin_session()):
		return redirect(url_for('login'))
	api_key = _get_session_api_key()
	if not api_key:
		return redirect(url_for('admin') if _is_admin_session() else url_for('login'))

	form_data = request.form if request.method == 'POST' else {}
	values = _build_values(form_data)
	error = None
	result = None
	notice = None
	confirm = False
	confirmed_ip = ''
	action = (form_data.get('action') or '').strip()
	confirm_display_values = _build_confirm_display_values(values)

	if request.method == 'POST' and action in ('confirm', 'create'):
		selected_subnets = {value for value, _ in CURRENT_SUBNET_OPTIONS}
		if values.get('target_subnet') not in selected_subnets and CURRENT_SUBNET_OPTIONS:
			values['target_subnet'] = CURRENT_SUBNET_OPTIONS[0][0]
		ticket_data, errors = _build_ticket_data(form_data)
		if errors:
			error = ' '.join(errors)
			db.append_log(
				'entry',
				_get_session_user_name(),
				'error',
				f'{action} validation error: {error}'
			)
		elif action == 'confirm':
			try:
				project_id = redmine.get_redmine_project_id(db.get_setting('project_name', ''), api_key=api_key)
				if project_id is None:
					raise RuntimeError(f'プロジェクトが見つかりません: {db.get_setting("project_name", "")}')
				confirmed_ip, _ = redmine.allocate_next_ip(
					project_id,
					ticket_data['target_subnet'],
					api_key=api_key,
				)
				confirm = True
				db.append_log(
					'entry',
					_get_session_user_name(),
					'info',
					f'confirm vm_name={vm_name} subnet={ticket_data["target_subnet"]} ip={confirmed_ip}'
				)
			except Exception as exc:
				error = str(exc)
				db.append_log(
					'entry',
					_get_session_user_name(),
					'error',
					f'confirm failed: {error}'
				)

		else:
			try:
				project_id = redmine.get_redmine_project_id(db.get_setting('project_name', ''), api_key=api_key)
				if project_id is None:
					raise RuntimeError(f'プロジェクトが見つかりません: {db.get_setting("project_name", "")}')
				confirmed_ip = (form_data.get('confirmed_ip') or '').strip()
				if not confirmed_ip:
					raise RuntimeError('確認画面の割当予定IPが取得できません。確認画面から再実行してください。')
				if redmine.is_ip_already_registered(project_id, confirmed_ip, api_key=api_key):
					raise RuntimeError(
						f'確認後に同一IP ({confirmed_ip}) が登録されました。'
						'入力画面に戻って再確認してください。'
					)
				result = redmine.create_redmine_ticket(project_id, ticket_data, confirmed_ip, api_key=api_key)
				if result.get('result') != 'success':
					error = result.get('message', 'チケット登録に失敗しました。')
					db.append_log(
						'entry',
						_get_session_user_name(),
						'error',
						f'create failed: {error}'
					)
				else:
					result['url'] = result.get('url') or _build_ticket_url(result.get('id'))
					result['subject'] = ticket_data.get('subject')
					result['vm_name'] = form_data.get('vm_name')
					result['target_subnet'] = ticket_data.get('target_subnet')
					db.append_log(
						'entry',
						_get_session_user_name(),
						'info',
						f'create success ticket_id={result.get("id")} vm_name={result.get("vm_name")} subnet={result.get("target_subnet")} ip={confirmed_ip}'
					)	
			except Exception as exc:
				error = str(exc)
				db.append_log(
					'entry',
					_get_session_user_name(),
					'error',
					f'create exception: {error}'
				)

	return render_template(
		'index.html',
		values=values,
		confirm_display_values=confirm_display_values,
		confirm=confirm,
		confirmed_ip=confirmed_ip,
		confirm_fields=CONFIRM_FIELDS,
		error=error,
		result=result,
		notice=notice,
		user_name=_get_session_user_name(),
		is_admin=_is_admin_session(),
		subnet_options=CURRENT_SUBNET_OPTIONS,
		vhost_options=sorted(CURRENT_VHOST_IP_DISPLAY_MAP.items(), key=lambda item: item[1]),
		os_options=CURRENT_OS_OPTIONS,
		usage_options=CURRENT_USAGE_OPTIONS,
		usage_selected={item.strip() for item in (values.get('usage') or '').split(',') if item.strip()},
		assignee_names=sorted(CURRENT_ASSIGNEE_NAME_TO_ID.keys()),
		template_options=CURRENT_VM_TEMPLATE_OPTIONS,
	)


if __name__ == '__main__':
	app.run(host='0.0.0.0', port=5000, debug=True)