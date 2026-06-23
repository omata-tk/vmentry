from flask import redirect, render_template, request, url_for

from core import db
from services import redmine
from web.auth_routes import get_session_api_key, get_session_user_name, is_admin_session
from web.state import (
    CURRENT_ASSIGNEE_NAME_TO_ID,
    CURRENT_OS_OPTIONS,
    CURRENT_SUBNET_OPTIONS,
    CURRENT_USAGE_OPTIONS,
    CURRENT_VHOST_IP_DISPLAY_MAP,
    CURRENT_VM_TEMPLATE_OPTIONS,
)


CONFIRM_FIELDS = [
    ("target_subnet", "対象サブネット"),
    ("subject", "Redmineチケット名"),
    ("assignee_name", "担当者"),
    ("vm_name", "仮想マシン名"),
    ("start_date", "開始日"),
    ("due_date", "期日"),
    ("description", "説明文"),
    ("os_value", "OS"),
    ("vhost_ip", "IPアドレス（仮想ホスト）"),
    ("os_user", "OSユーザー"),
    ("os_password", "OSパスワード"),
    ("os_product_key", "OSプロダクトキー"),
    ("os_product_key_owner", "OSプロダクトキー所有者"),
    ("usage", "利用用途"),
    ("usage_other", "利用用途（その他）"),
    ("system_login_info", "システムログイン情報"),
    ("initial_builder", "初期構築担当者"),
    ("notes", "特記事項"),
    ("vm_template", "VMテンプレート"),
    ("memory", "メモリ(GB)"),
    ("disk", "ディスク(GB)"),
    ("cpu", "CPU数"),
    ("switch", "仮想スイッチ"),
]


def form_defaults():
    target_default = CURRENT_SUBNET_OPTIONS[0][0] if CURRENT_SUBNET_OPTIONS else ""
    return {
        "target_subnet": target_default,
        "subject": db.get_setting("form_default_subject", ""),
        "assignee_name": "",
        "start_date": "",
        "due_date": "",
        "description": "",
        "vm_name": "",
        "os_value": "",
        "os_product_key": "",
        "os_product_key_owner": "",
        "vhost_ip": "",
        "os_user": db.get_setting("form_default_os_user", ""),
        "os_password": "",
        "usage": "",
        "usage_other": "",
        "system_login_info": "",
        "initial_builder": "",
        "notes": "",
        "deploy_type": "template",
        "vm_template": "",
        "memory": "",
        "disk": "",
        "cpu": "",
        "switch": "",
        "debug_ticket_only": "",
    }


def build_values(form_data):
    defaults = form_defaults()
    values = {}
    for key, default in defaults.items():
        if key == "usage" and hasattr(form_data, "getlist"):
            checked_values = [
                item.strip()
                for item in form_data.getlist("usage")
                if isinstance(item, str) and item.strip()
            ]
            if checked_values:
                values[key] = ", ".join(checked_values)
                continue
        values[key] = form_data.get(key, default)

    if not values.get("target_subnet") and CURRENT_SUBNET_OPTIONS:
        values["target_subnet"] = CURRENT_SUBNET_OPTIONS[0][0]

    reverse_map = {v: k for k, v in CURRENT_VHOST_IP_DISPLAY_MAP.items()}
    vhost_value = values.get("vhost_ip")
    if vhost_value in reverse_map:
        values["vhost_ip"] = reverse_map[vhost_value]

    return values


def build_confirm_display_values(values):
    display_values = dict(values)
    os_label_map = {value: label for value, label in CURRENT_OS_OPTIONS}
    vhost_label_map = dict(CURRENT_VHOST_IP_DISPLAY_MAP)
    display_values["os_value"] = os_label_map.get(values.get("os_value", ""), values.get("os_value", ""))
    display_values["vhost_ip"] = vhost_label_map.get(values.get("vhost_ip", ""), values.get("vhost_ip", ""))
    return display_values


def build_ticket_data(form_data):
    errors = []
    selected_subnets = {value for value, _ in CURRENT_SUBNET_OPTIONS}

    debug_ticket_only = (form_data.get("debug_ticket_only") or "").strip() == "on"

    target_subnet = (form_data.get("target_subnet") or "").strip()
    subject = (form_data.get("subject") or "").strip()
    vm_name = (form_data.get("vm_name") or "").strip()

    if not target_subnet:
        errors.append("対象サブネットを選択してください。")
    elif selected_subnets and target_subnet not in selected_subnets:
        errors.append("対象サブネットは候補から選択してください。")

    if not subject:
        errors.append("Redmineチケット名は必須です。")

    if not debug_ticket_only and not vm_name:
        errors.append("仮想マシン名は必須です。")

    deploy_type = (form_data.get("deploy_type") or "").strip()
    vm_template = (form_data.get("vm_template") or "").strip()

    memory = (form_data.get("memory") or "").strip()
    disk = (form_data.get("disk") or "").strip()
    cpu = (form_data.get("cpu") or "").strip()

    if deploy_type and deploy_type not in ("template", "manual"):
        errors.append("作成方法が不正です。")

    if not debug_ticket_only:
        if deploy_type == "template":
            if not vm_template:
                errors.append("VMテンプレートを選択してください。")
        elif deploy_type == "manual":
            if not memory or not disk or not cpu:
                errors.append("手動作成の場合はメモリ・ディスク・CPUが必須です。")
            else:
                try:
                    int(memory)
                    int(disk)
                    int(cpu)
                except ValueError:
                    errors.append("メモリ・ディスク・CPUは数値で入力してください。")

    assignee_name = (form_data.get("assignee_name") or "").strip()
    assigned_to_id = None
    if assignee_name:
        if assignee_name not in CURRENT_ASSIGNEE_NAME_TO_ID:
            errors.append("担当者は登録済みの名前を入力してください。")
        else:
            assigned_to_id = CURRENT_ASSIGNEE_NAME_TO_ID[assignee_name]

    usage_values = []
    if hasattr(form_data, "getlist"):
        usage_values = [
            item.strip()
            for item in form_data.getlist("usage")
            if isinstance(item, str) and item.strip()
        ]
    if not usage_values:
        usage_raw = (form_data.get("usage") or "").strip()
        usage_values = [item.strip() for item in usage_raw.split(",") if item.strip()]

    ticket_data = {
        "target_subnet": target_subnet,
        "subject": subject,
        "tracker_id": db.get_int_setting("default_tracker_id", 12),
        "status_id": db.get_int_setting("default_status_id", 13),
        "priority_id": db.get_int_setting("default_priority_id", 2),
        "assigned_to_id": assigned_to_id,
        "start_date": (form_data.get("start_date") or "").strip() or None,
        "due_date": (form_data.get("due_date") or "").strip() or None,
        "description": (form_data.get("description") or "").strip(),
        "custom_fields": [
            {"id": 45, "value": vm_name},
            {"id": 43, "value": (form_data.get("os_value") or "").strip()},
            {"id": 46, "value": (form_data.get("os_product_key") or "").strip()},
            {"id": 50, "value": (form_data.get("os_product_key_owner") or "").strip()},
            {"id": 53, "value": (form_data.get("vhost_ip") or "").strip()},
            {"id": 55, "value": (form_data.get("os_user") or "").strip()},
            {"id": 56, "value": (form_data.get("os_password") or "").strip()},
            {"id": 57, "value": usage_values},
            {"id": 58, "value": (form_data.get("usage_other") or "").strip()},
            {"id": 59, "value": (form_data.get("system_login_info") or "").strip()},
            {"id": 98, "value": (form_data.get("initial_builder") or "").strip()},
            {"id": 35, "value": (form_data.get("notes") or "").strip()},
        ],
        "debug_ticket_only": debug_ticket_only,
    }
    return ticket_data, errors


def build_ticket_url(ticket_id):
    if not ticket_id:
        return None
    base = (db.get_setting("redmine_url", "") or "").rstrip("/")
    if not base:
        return None
    return f"{base}/issues/{ticket_id}"


def register_entry_routes(app):
    @app.route("/", methods=["GET"])
    def index():
        if not (get_session_api_key() or is_admin_session()):
            return redirect(url_for("login"))

        return render_template(
            "index.html",
            user_name=get_session_user_name(),
            is_admin=is_admin_session(),
        )

    @app.route("/entry", methods=["GET", "POST"])
    def entry():
        if not (get_session_api_key() or is_admin_session()):
            return redirect(url_for("login"))

        api_key = get_session_api_key()
        if not api_key:
            return redirect(url_for("admin") if is_admin_session() else url_for("login"))

        form_data = request.form if request.method == "POST" else {}
        values = build_values(form_data)
        error = None
        result = None
        notice = None
        confirm = False
        confirmed_ip = ""
        action = (form_data.get("action") or "").strip()
        confirm_display_values = build_confirm_display_values(values)

        if request.method == "POST" and action in ("confirm", "create"):
            selected_subnets = {value for value, _ in CURRENT_SUBNET_OPTIONS}
            if values.get("target_subnet") not in selected_subnets and CURRENT_SUBNET_OPTIONS:
                values["target_subnet"] = CURRENT_SUBNET_OPTIONS[0][0]

            ticket_data, errors = build_ticket_data(form_data)

            if errors:
                error = " ".join(errors)
                db.append_log(
                    "entry",
                    get_session_user_name(),
                    "error",
                    f"{action} validation error: {error}",
                )
            elif action == "confirm":
                try:
                    project_id = redmine.get_redmine_project_id(db.get_setting("project_name", ""), api_key=api_key)
                    if project_id is None:
                        raise RuntimeError(
                            f'プロジェクトが見つかりません: {db.get_setting("project_name", "")}'
                        )

                    confirmed_ip, _ = redmine.allocate_next_ip(
                        project_id,
                        ticket_data["target_subnet"],
                        api_key=api_key,
                    )
                    confirm = True
                    db.append_log(
                        "entry",
                        get_session_user_name(),
                        "info",
                        f'confirm vm_name={form_data.get("vm_name", "")} subnet={ticket_data["target_subnet"]} ip={confirmed_ip}',
                    )
                except Exception as exc:
                    error = str(exc)
                    db.append_log(
                        "entry",
                        get_session_user_name(),
                        "error",
                        f"confirm failed: {error}",
                    )
            else:
                try:
                    project_id = redmine.get_redmine_project_id(db.get_setting("project_name", ""), api_key=api_key)
                    if project_id is None:
                        raise RuntimeError(
                            f'プロジェクトが見つかりません: {db.get_setting("project_name", "")}'
                        )

                    confirmed_ip = (form_data.get("confirmed_ip") or "").strip()
                    if not confirmed_ip:
                        raise RuntimeError("確認画面の割当予定IPが取得できません。確認画面から再実行してください。")

                    if redmine.is_ip_already_registered(project_id, confirmed_ip, api_key=api_key):
                        raise RuntimeError(
                            f"確認後に同一IP ({confirmed_ip}) が登録されました。"
                            "入力画面に戻って再確認してください。"
                        )

                    result = redmine.create_redmine_ticket(
                        project_id,
                        ticket_data,
                        confirmed_ip,
                        api_key=api_key,
                    )
                    if result.get("result") != "success":
                        error = result.get("message", "チケット登録に失敗しました。")
                        db.append_log(
                            "entry",
                            get_session_user_name(),
                            "error",
                            f"create failed: {error}",
                        )
                    else:
                        result["url"] = result.get("url") or build_ticket_url(result.get("id"))
                        result["subject"] = ticket_data.get("subject")
                        result["vm_name"] = form_data.get("vm_name")
                        result["target_subnet"] = ticket_data.get("target_subnet")
                        db.append_log(
                            "entry",
                            get_session_user_name(),
                            "info",
                            f'create success ticket_id={result.get("id")} vm_name={result.get("vm_name")} subnet={result.get("target_subnet")} ip={confirmed_ip}',
                        )
                except Exception as exc:
                    error = str(exc)
                    db.append_log(
                        "entry",
                        get_session_user_name(),
                        "error",
                        f"create exception: {error}",
                    )

        return render_template(
            "entry.html",
            values=values,
            confirm_display_values=confirm_display_values,
            confirm=confirm,
            confirmed_ip=confirmed_ip,
            confirm_fields=CONFIRM_FIELDS,
            error=error,
            result=result,
            notice=notice,
            user_name=get_session_user_name(),
            is_admin=is_admin_session(),
            subnet_options=CURRENT_SUBNET_OPTIONS,
            vhost_options=sorted(CURRENT_VHOST_IP_DISPLAY_MAP.items(), key=lambda item: item[1]),
            os_options=CURRENT_OS_OPTIONS,
            usage_options=CURRENT_USAGE_OPTIONS,
            usage_selected={item.strip() for item in (values.get("usage") or "").split(",") if item.strip()},
            assignee_names=sorted(CURRENT_ASSIGNEE_NAME_TO_ID.keys()),
            template_options=CURRENT_VM_TEMPLATE_OPTIONS,
        )
