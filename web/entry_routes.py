import json

from flask import jsonify, redirect, render_template, request, url_for

from core import db
from services import hyperv, redmine
from web.auth_routes import get_session_api_key, get_session_user_name, is_admin_session
from web.state import (
    CURRENT_ASSIGNEE_NAME_TO_ID,
    CURRENT_OS_OPTIONS,
    CURRENT_SUBNET_OPTIONS,
    CURRENT_USAGE_OPTIONS,
    CURRENT_VHOST_IP_DISPLAY_MAP,
    CURRENT_VM_SWITCH_OPTIONS,
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
    ("vcpu_count", "仮想プロセッサ カウント"),
    ("enable_nested_virtualization", "入れ子になった仮想化"),
    ("startup_memory", "起動メモリ"),
    ("memory_unit", "メモリ単位"),
    ("use_dynamic_memory", "動的メモリ"),
    ("virtual_switch", "仮想スイッチ"),
    ("manual_disks_json", "ストレージ構成"),
    ("os_install_mode", "OSインストール方法"),
    ("os_iso_path", "OS ISO パス"),
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
        "vcpu_count": "",
        "enable_nested_virtualization": "",
        "startup_memory": "",
        "memory_unit": "GB",
        "use_dynamic_memory": "",
        "virtual_switch": "",
        "manual_disks_json": "",
        "os_install_mode": "later",
        "os_iso_path": "",
        "debug_ticket_only": "",
    }


def build_values(form_data):
    defaults = form_defaults()
    values = {}
    for key, default in defaults.items():
        if key == "usage" and hasattr(form_data, "getlist"):
            checked_values = extract_usage_values(form_data)
            if checked_values:
                values[key] = "\n".join(checked_values)
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
    vm_switch_label_map = {value: label for value, label in CURRENT_VM_SWITCH_OPTIONS}
    display_values["os_value"] = os_label_map.get(values.get("os_value", ""), values.get("os_value", ""))
    display_values["vhost_ip"] = vhost_label_map.get(values.get("vhost_ip", ""), values.get("vhost_ip", ""))
    display_values["virtual_switch"] = vm_switch_label_map.get(
        values.get("virtual_switch", ""), values.get("virtual_switch", "")
    )
    display_values["enable_nested_virtualization"] = "有効" if values.get("enable_nested_virtualization") == "on" else "無効"
    display_values["use_dynamic_memory"] = "有効" if values.get("use_dynamic_memory") == "on" else "無効"
    display_values["os_install_mode"] = (
        "後でオペレーティングシステムをインストールする"
        if values.get("os_install_mode") == "later"
        else "画像ファイル（.iso）からオペレーティングシステムをインストールする"
    )

    startup_memory = (values.get("startup_memory") or "").strip()
    memory_unit = (values.get("memory_unit") or "").strip()
    if startup_memory:
        display_values["startup_memory"] = f"{startup_memory} {memory_unit}".strip()

    display_values["manual_disks_json"] = _build_storage_confirm_text(values.get("manual_disks_json", ""))
    return display_values


def parse_manual_disks_json(raw_value):
    text = (raw_value or "").strip()
    if not text:
        return [], None

    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return [], "ストレージ構成の形式が不正です。"

    if not isinstance(parsed, list):
        return [], "ストレージ構成の形式が不正です。"

    disks = []
    for idx, item in enumerate(parsed, start=1):
        if not isinstance(item, dict):
            return [], "ストレージ構成の形式が不正です。"
        disk_type = (item.get("type") or "empty").strip()
        if disk_type not in ("empty", "existing"):
            return [], "ストレージ構成の形式が不正です。"

        size_gb = str(item.get("size_gb") or "").strip()
        path = str(item.get("path") or "").strip()
        disks.append(
            {
                "index": idx,
                "type": disk_type,
                "size_gb": size_gb,
                "path": path,
            }
        )
    return disks, None


def _build_storage_confirm_text(raw_value):
    disks, parse_error = parse_manual_disks_json(raw_value)
    if parse_error:
        return "-"
    if not disks:
        return "-"

    lines = []
    for disk in disks:
        if disk["type"] == "existing":
            lines.append(f"新しいディスク{disk['index']}: 既存VHD ({disk['path'] or '-'})")
        else:
            lines.append(f"新しいディスク{disk['index']}: 空ディスク ({disk['size_gb'] or '-'} GB)")
    return "\n".join(lines)


def extract_usage_values(form_data):
    usage_values = []
    option_values = {value for value, _ in CURRENT_USAGE_OPTIONS}

    def _append_usage(raw_value):
        if raw_value is None:
            return
        text = str(raw_value).strip()
        if not text:
            return

        # 旧確認画面の hidden では複数値がカンマ結合されるため、候補値に無い場合のみ分割する。
        if text in option_values or "," not in text:
            usage_values.append(text)
            return

        usage_values.extend(item.strip() for item in text.split(",") if item.strip())

    if hasattr(form_data, "getlist"):
        for item in form_data.getlist("usage"):
            _append_usage(item)
    elif hasattr(form_data, "get"):
        _append_usage(form_data.get("usage"))

    # 重複を除去しつつ順序を維持する。
    seen = set()
    deduped = []
    for value in usage_values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


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

    vcpu_count = (form_data.get("vcpu_count") or "").strip()
    startup_memory = (form_data.get("startup_memory") or "").strip()
    memory_unit = (form_data.get("memory_unit") or "").strip()
    virtual_switch = (form_data.get("virtual_switch") or "").strip()
    os_install_mode = (form_data.get("os_install_mode") or "").strip() or "later"
    os_iso_path = (form_data.get("os_iso_path") or "").strip()
    manual_disks_raw = (form_data.get("manual_disks_json") or "").strip()

    if deploy_type and deploy_type not in ("template", "manual"):
        errors.append("作成方法が不正です。")

    if not debug_ticket_only:
        if deploy_type == "template":
            if not vm_template:
                errors.append("VMテンプレートを選択してください。")
        elif deploy_type == "manual":
            if not vcpu_count or not startup_memory:
                errors.append("手動作成の場合は仮想プロセッサ カウントと起動メモリが必須です。")
            else:
                try:
                    if int(vcpu_count) <= 0 or int(startup_memory) <= 0:
                        errors.append("仮想プロセッサ カウントと起動メモリは1以上で入力してください。")
                except ValueError:
                    errors.append("仮想プロセッサ カウントと起動メモリは数値で入力してください。")

            if memory_unit not in ("GB", "MB"):
                errors.append("メモリ単位はGBまたはMBを選択してください。")

            switch_values = {value for value, _ in CURRENT_VM_SWITCH_OPTIONS}
            if not virtual_switch:
                errors.append("仮想スイッチを選択してください。")
            elif switch_values and virtual_switch not in switch_values:
                errors.append("仮想スイッチは候補から選択してください。")

            manual_disks, parse_error = parse_manual_disks_json(manual_disks_raw)
            if parse_error:
                errors.append(parse_error)
            elif not manual_disks:
                errors.append("ストレージは最低1件設定してください。")
            else:
                for disk in manual_disks:
                    if disk["type"] == "existing":
                        if not disk["path"]:
                            errors.append(f"新しいディスク{disk['index']} のパスを入力してください。")
                    else:
                        if not disk["size_gb"]:
                            errors.append(f"新しいディスク{disk['index']} のサイズ（GB）を入力してください。")
                            continue
                        try:
                            if int(disk["size_gb"]) <= 0:
                                errors.append(f"新しいディスク{disk['index']} のサイズ（GB）は1以上で入力してください。")
                        except ValueError:
                            errors.append(f"新しいディスク{disk['index']} のサイズ（GB）は数値で入力してください。")

            if os_install_mode not in ("later", "iso"):
                errors.append("オペレーティングシステムの設定が不正です。")
            elif os_install_mode == "iso" and not os_iso_path:
                errors.append("ISOインストールを選択した場合はISOファイルのパスを入力してください。")

    assignee_name = (form_data.get("assignee_name") or "").strip()
    assigned_to_id = None
    if assignee_name:
        if assignee_name not in CURRENT_ASSIGNEE_NAME_TO_ID:
            errors.append("担当者は登録済みの名前を入力してください。")
        else:
            assigned_to_id = CURRENT_ASSIGNEE_NAME_TO_ID[assignee_name]

    usage_values = extract_usage_values(form_data)

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
        "vm_config": {
            "deploy_type": deploy_type,
            "vm_template": vm_template,
            "manual": {
                "vcpu_count": vcpu_count,
                "enable_nested_virtualization": (form_data.get("enable_nested_virtualization") or "").strip() == "on",
                "startup_memory": startup_memory,
                "memory_unit": memory_unit,
                "use_dynamic_memory": (form_data.get("use_dynamic_memory") or "").strip() == "on",
                "virtual_switch": virtual_switch,
                "disks": parse_manual_disks_json(manual_disks_raw)[0],
                "os_install_mode": os_install_mode,
                "os_iso_path": os_iso_path,
            },
        },
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


def _normalize_x_drive_path(raw_path):
    text = (raw_path or "").strip().replace("/", "\\")
    if not text:
        return "X:\\"

    if text.lower() == "x:":
        return "X:\\"

    if not text.lower().startswith("x:\\"):
        return None

    return text


def _configured_hyperv_hosts():
    settings = db.get_all_settings()
    hosts = []
    for idx in range(1, 4):
        ip = (settings.get(f"hyperv_host{idx}_ip") or "").strip()
        user = (settings.get(f"hyperv_host{idx}_user") or "").strip()
        password = db._decrypt_password(settings.get(f"hyperv_host{idx}_password") or "")
        hosts.append(
            {
                "index": idx,
                "ip": ip,
                "user": user,
                "password": password,
            }
        )
    return hosts


def _filter_entries_for_browse(entries, browse_kind):
    if browse_kind == "iso":
        allowed_ext = {".iso"}
    else:
        allowed_ext = {".vhd", ".vhdx"}

    filtered = []
    for item in entries:
        if item.get("is_dir"):
            filtered.append(item)
            continue
        if item.get("ext") in allowed_ext:
            filtered.append(item)
    return filtered


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
            usage_selected=extract_usage_values(form_data) if request.method == "POST" else extract_usage_values(values),
            assignee_names=sorted(CURRENT_ASSIGNEE_NAME_TO_ID.keys()),
            template_options=CURRENT_VM_TEMPLATE_OPTIONS,
            vm_switch_options=CURRENT_VM_SWITCH_OPTIONS,
        )

    @app.route("/entry/host-browse", methods=["GET"])
    def entry_host_browse():
        if not (get_session_api_key() or is_admin_session()):
            return jsonify({"ok": False, "message": "認証が必要です。"}), 401

        browse_kind = (request.args.get("kind") or "").strip().lower()
        if browse_kind not in ("iso", "vhd"):
            return jsonify({"ok": False, "message": "参照種別が不正です。"}), 400

        normalized_path = _normalize_x_drive_path(request.args.get("path") or "")
        if normalized_path is None:
            return jsonify({"ok": False, "message": "Xドライブ配下のパスのみ参照できます。"}), 400

        host_errors = []
        any_host_configured = False

        for host in _configured_hyperv_hosts():
            host_label = f"ホスト{host['index']}"
            if not host["ip"] or not host["user"] or not host["password"]:
                host_errors.append(f"{host_label}: 接続設定が不足しています。")
                continue

            any_host_configured = True
            try:
                browse_result = hyperv.browse_host_path(
                    host["ip"],
                    host["user"],
                    host["password"],
                    normalized_path,
                )
                entries = _filter_entries_for_browse(browse_result.get("entries", []), browse_kind)

                warning = ""
                if host_errors:
                    warning = "先行ホスト接続失敗: " + " ".join(host_errors)

                return jsonify(
                    {
                        "ok": True,
                        "host": host_label,
                        "host_ip": host["ip"],
                        "current_path": browse_result.get("current_path", normalized_path),
                        "parent_path": browse_result.get("parent_path", normalized_path),
                        "entries": entries,
                        "warning": warning,
                    }
                )
            except Exception as exc:
                host_errors.append(f"{host_label}: {str(exc)}")

        if not any_host_configured:
            message = "管理者設定のホスト1〜3に接続情報がありません。"
        else:
            message = "ホストサーバに接続できませんでした。"
            if host_errors:
                message = f"{message} " + " ".join(host_errors)

        return jsonify({"ok": False, "message": message}), 503
