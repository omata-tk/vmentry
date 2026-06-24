from flask import redirect, render_template, request, url_for

from core import db
from services import hyperv, redmine
from web.auth_routes import get_session_user_name, is_admin_session
from web.state import (
    CURRENT_ASSIGNEE_NAME_TO_ID,
    CURRENT_OS_OPTIONS,
    CURRENT_SUBNET_OPTIONS,
    CURRENT_USAGE_OPTIONS,
    CURRENT_VHOST_IP_DISPLAY_MAP,
    CURRENT_VM_TEMPLATE_OPTIONS,
    DEFAULT_OS_OPTIONS,
    DEFAULT_SUBNET_OPTIONS,
    DEFAULT_USAGE_OPTIONS,
    DEFAULT_VM_TEMPLATE_OPTIONS,
)


def refresh_master_data_from_redmine(api_key, executed_by=None):
    executor_name = (executed_by or "").strip() or get_session_user_name()
    latest = redmine.fetch_latest_form_master(db.get_setting("project_name", ""), api_key=api_key)

    CURRENT_ASSIGNEE_NAME_TO_ID.clear()
    CURRENT_ASSIGNEE_NAME_TO_ID.update(latest.get("assignee_name_to_id", {}))

    CURRENT_VHOST_IP_DISPLAY_MAP.clear()
    CURRENT_VHOST_IP_DISPLAY_MAP.update(latest.get("vhost_ip_display_map", {}))

    subnet_prefixes = latest.get("subnet_prefixes", [])
    CURRENT_SUBNET_OPTIONS.clear()
    if subnet_prefixes:
        for subnet_prefix in subnet_prefixes:
            CURRENT_SUBNET_OPTIONS.append((subnet_prefix, subnet_prefix))
    else:
        CURRENT_SUBNET_OPTIONS.extend(DEFAULT_SUBNET_OPTIONS)

    latest_os_options = latest.get("os_options", [])
    CURRENT_OS_OPTIONS.clear()
    if latest_os_options:
        for value, label in latest_os_options:
            CURRENT_OS_OPTIONS.append((value, label))
    else:
        CURRENT_OS_OPTIONS.extend(DEFAULT_OS_OPTIONS)

    latest_usage_options = latest.get("usage_options", [])
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
            "subnet",
            [(item[0], item[1]) for item in CURRENT_SUBNET_OPTIONS],
            updated_by=executor_name,
        )
        db.replace_master_options(
            "assignee",
            [(str(user_id), name) for name, user_id in CURRENT_ASSIGNEE_NAME_TO_ID.items()],
            updated_by=executor_name,
        )
        db.replace_master_options(
            "vhost",
            [(value, label) for value, label in CURRENT_VHOST_IP_DISPLAY_MAP.items()],
            updated_by=executor_name,
        )

        vm_templates = hyperv.get_vm_templates()
        db.replace_master_options(
            "vm_template",
            vm_templates,
            updated_by=executor_name,
        )
    except Exception as exc:
        config_sync_error = str(exc)
        db.append_log("sync", executor_name, "error", config_sync_error)
    else:
        db.append_log("sync", executor_name, "info", "最新情報取得に成功しました。")

    notice_message = (
        f"担当者 {len(CURRENT_ASSIGNEE_NAME_TO_ID)} 件、"
        f"仮想ホスト候補 {len(CURRENT_VHOST_IP_DISPLAY_MAP)} 件、"
        f"サブネット候補 {len(CURRENT_SUBNET_OPTIONS)} 件、"
        f"OS候補 {len(CURRENT_OS_OPTIONS)} 件、"
        f"利用用途候補 {len(CURRENT_USAGE_OPTIONS)} 件を反映しました。"
    )
    if config_sync_error:
        notice_message += f" SQLite 更新は失敗: {config_sync_error}"
    else:
        notice_message += " SQLite へ候補を保存しました。"

    warnings = [item for item in latest.get("warnings", []) if isinstance(item, str) and item.strip()]
    if warnings:
        notice_message += " 一部項目は取得不可のため既存設定を使用しました。"

    return notice_message


def build_hyperv_hosts_from_settings():
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


def refresh_vm_templates(executed_by=None):
    executor_name = (executed_by or "").strip() or get_session_user_name()
    try:
        hosts = build_hyperv_hosts_from_settings()
        vm_templates, host_results = hyperv.get_vm_templates_from_hosts(hosts)

        for result in host_results:
            status = result.get("status", "info")
            message = result.get("message", "")
            if status in ("success", "skipped"):
                db.append_log("sync", executor_name, "info", message)
            else:
                db.append_log("sync", executor_name, "error", message)

        CURRENT_VM_TEMPLATE_OPTIONS.clear()
        if vm_templates:
            CURRENT_VM_TEMPLATE_OPTIONS.extend(vm_templates)
            db.replace_master_options(
                "vm_template",
                vm_templates,
                updated_by=executor_name,
            )
            return f"VMテンプレート {len(vm_templates)} 件を反映しました。"

        CURRENT_VM_TEMPLATE_OPTIONS.extend(DEFAULT_VM_TEMPLATE_OPTIONS)
        return "VMテンプレートを取得できませんでした。既存設定を使用します。"

    except Exception as exc:
        db.append_log("sync", executor_name, "error", f"Hyper-V取得失敗: {str(exc)}")
        raise


def register_admin_routes(app):
    @app.route("/admin", methods=["GET", "POST"])
    def admin():
        if not is_admin_session():
            return redirect(url_for("login"))

        error = None
        message = None
        action = (request.form.get("action") or "").strip() if request.method == "POST" else ""

        if request.method == "POST" and action == "save_settings":
            try:
                keys = [
                    "redmine_url",
                    "project_name",
                    "default_tracker_id",
                    "default_status_id",
                    "default_priority_id",
                    "default_start_octet",
                    "form_default_subject",
                    "form_default_os_user",
                ]
                payload = {key: (request.form.get(key) or "").strip() for key in keys}
                db.set_settings(payload, updated_by=get_session_user_name())
                new_key = (request.form.get("admin_magic_key") or "").strip()
                if new_key:
                    db.set_admin_key(new_key)
                message = "設定を保存しました。"
            except Exception as exc:
                error = str(exc)

        if request.method == "POST" and action == "save_hyperv_hosts":
            try:
                payload = {}
                for i in range(1, 4):
                    ip = (request.form.get(f"hyperv_host{i}_ip") or "").strip()
                    user = (request.form.get(f"hyperv_host{i}_user") or "").strip()
                    password = (request.form.get(f"hyperv_host{i}_password") or "").strip()

                    payload[f"hyperv_host{i}_ip"] = ip
                    payload[f"hyperv_host{i}_user"] = user

                    if password:
                        payload[f"hyperv_host{i}_password"] = db._encrypt_password(password)
                    elif ip or user:
                        payload[f"hyperv_host{i}_password"] = db.get_setting(f"hyperv_host{i}_password", "")

                db.set_settings(payload, updated_by=get_session_user_name())
                message = "Hyper-Vホスト設定を保存しました。"
            except Exception as exc:
                error = str(exc)

        if request.method == "POST" and action == "refresh_vm_templates":
            executor_name = get_session_user_name()
            try:
                message = refresh_vm_templates(executed_by=executor_name)
            except Exception as exc:
                error = str(exc)

        settings_display = dict(db.get_all_settings())
        for i in range(1, 4):
            if settings_display.get(f"hyperv_host{i}_password"):
                settings_display[f"hyperv_host{i}_password"] = ""

        return render_template(
            "admin.html",
            settings=settings_display,
            logs=db.get_recent_logs(20),
            message=message,
            error=error,
            user_name=get_session_user_name(),
        )
