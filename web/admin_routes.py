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
    CURRENT_VM_SWITCH_OPTIONS,
    CURRENT_VM_TEMPLATE_OPTIONS,
    DEFAULT_OS_OPTIONS,
    DEFAULT_SUBNET_OPTIONS,
    DEFAULT_USAGE_OPTIONS,
    DEFAULT_VM_SWITCH_OPTIONS,
    DEFAULT_VM_TEMPLATE_OPTIONS,
)


def refresh_master_data_from_redmine(api_key, executed_by=None):
    executor_name = (executed_by or "").strip() or get_session_user_name()
    latest = redmine.fetch_latest_form_master(db.get_setting("project_name", ""), api_key=api_key)
    hidden_subnets = db.get_hidden_subnet_set()

    CURRENT_ASSIGNEE_NAME_TO_ID.clear()
    CURRENT_ASSIGNEE_NAME_TO_ID.update(latest.get("assignee_name_to_id", {}))

    CURRENT_VHOST_IP_DISPLAY_MAP.clear()
    CURRENT_VHOST_IP_DISPLAY_MAP.update(latest.get("vhost_ip_display_map", {}))

    subnet_prefixes = latest.get("subnet_prefixes", [])
    CURRENT_SUBNET_OPTIONS.clear()
    if subnet_prefixes:
        for subnet_prefix in subnet_prefixes:
            if subnet_prefix in hidden_subnets:
                continue
            CURRENT_SUBNET_OPTIONS.append((subnet_prefix, subnet_prefix))
    else:
        CURRENT_SUBNET_OPTIONS.extend(
            [
                (value, label)
                for value, label in DEFAULT_SUBNET_OPTIONS
                if (value or "").strip() not in hidden_subnets
            ]
        )

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
        vm_switches, switch_results = hyperv.get_vm_switches_from_hosts(hosts)

        for result in host_results:
            status = result.get("status", "info")
            message = result.get("message", "")
            if status in ("success", "skipped"):
                db.append_log("sync", executor_name, "info", message)
            else:
                db.append_log("sync", executor_name, "error", message)

        for result in switch_results:
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
            template_message = f"VMテンプレート {len(vm_templates)} 件を反映しました。"
        else:
            CURRENT_VM_TEMPLATE_OPTIONS.extend(DEFAULT_VM_TEMPLATE_OPTIONS)
            template_message = "VMテンプレートを取得できませんでした。既存設定を使用します。"

        CURRENT_VM_SWITCH_OPTIONS.clear()
        if vm_switches:
            CURRENT_VM_SWITCH_OPTIONS.extend(vm_switches)
            db.replace_master_options(
                "vm_switch",
                vm_switches,
                updated_by=executor_name,
            )
            switch_message = f"仮想スイッチ {len(vm_switches)} 件を反映しました。"
        else:
            CURRENT_VM_SWITCH_OPTIONS.extend(DEFAULT_VM_SWITCH_OPTIONS)
            switch_message = "仮想スイッチを取得できませんでした。既存設定を使用します。"

        return f"{template_message} {switch_message}"

    except Exception as exc:
        db.append_log("sync", executor_name, "error", f"Hyper-V取得失敗: {str(exc)}")
        raise


def _is_valid_subnet_prefix(prefix_text):
    text = (prefix_text or "").strip()
    parts = text.split(".")
    if len(parts) != 3:
        return False
    if not all(part.isdigit() for part in parts):
        return False
    try:
        octets = [int(part) for part in parts]
    except ValueError:
        return False
    return all(0 <= value <= 255 for value in octets)


def _normalize_vlan_text(vlan_text):
    text = (vlan_text or "").strip()
    if not text:
        return "", None
    if not text.isdigit():
        return "", "VLAN IDは数値で入力してください。"
    vlan_int = int(text)
    if vlan_int < 1 or vlan_int > 4094:
        return "", "VLAN IDは1から4094の範囲で入力してください。"
    return str(vlan_int), None


def _is_valid_ipv4_text(ip_text):
    text = (ip_text or "").strip()
    parts = text.split(".")
    if len(parts) != 4:
        return False
    if not all(part.isdigit() for part in parts):
        return False
    try:
        octets = [int(part) for part in parts]
    except ValueError:
        return False
    return all(0 <= value <= 255 for value in octets)


def _normalize_gateway_text(gateway_text):
    text = (gateway_text or "").strip()
    if not text:
        return "", None
    if not _is_valid_ipv4_text(text):
        return "", "ゲートウェイはIPv4形式で入力してください。"
    return text, None


def _normalize_dns_text(dns_text):
    text = (dns_text or "").strip()
    if not text:
        return "", None

    items = [item.strip() for item in text.split(",") if item.strip()]
    if not items:
        return "", None

    invalid_items = [item for item in items if not _is_valid_ipv4_text(item)]
    if invalid_items:
        return "", f"DNS はIPv4形式をカンマ区切りで入力してください: {', '.join(invalid_items)}"
    return ", ".join(items), None


def _sort_subnet_prefixes(prefixes):
    def _sort_key(prefix):
        try:
            return tuple(int(part) for part in prefix.split("."))
        except ValueError:
            return (999, 999, 999)

    return sorted(prefixes, key=_sort_key)


def _build_subnet_vlan_rows():
    hidden_subnets = db.get_hidden_subnet_set()
    current_subnets = [value for value, _ in CURRENT_SUBNET_OPTIONS if (value or "").strip()]
    subnet_vlan_map = db.get_subnet_vlan_map()
    subnet_gateway_map = db.get_subnet_gateway_map()
    subnet_dns_map = db.get_subnet_dns_map()
    all_subnets = []
    seen = set()

    for subnet in current_subnets:
        if subnet in hidden_subnets:
            continue
        if subnet in seen:
            continue
        seen.add(subnet)
        all_subnets.append(subnet)

    for subnet in _sort_subnet_prefixes(subnet_vlan_map.keys()):
        if subnet in hidden_subnets:
            continue
        if subnet in seen:
            continue
        seen.add(subnet)
        all_subnets.append(subnet)

    rows = []
    for subnet in all_subnets:
        rows.append(
            {
                "subnet_prefix": subnet,
                "vlan_id": (subnet_vlan_map.get(subnet) or "").strip(),
                "gateway": (subnet_gateway_map.get(subnet) or "").strip(),
                "dns": (subnet_dns_map.get(subnet) or "").strip(),
            }
        )

    rows.append({"subnet_prefix": "", "vlan_id": "", "gateway": "", "dns": ""})
    return rows


def _build_rows_from_form(form):
    prefixes = form.getlist("subnet_prefix")
    vlans = form.getlist("subnet_vlan_id")
    gateways = form.getlist("subnet_gateway")
    dns_values = form.getlist("subnet_dns")
    row_count = max(len(prefixes), len(vlans), len(gateways), len(dns_values), 1)
    rows = []
    for idx in range(row_count):
        rows.append(
            {
                "subnet_prefix": (prefixes[idx] if idx < len(prefixes) else "").strip(),
                "vlan_id": (vlans[idx] if idx < len(vlans) else "").strip(),
                "gateway": (gateways[idx] if idx < len(gateways) else "").strip(),
                "dns": (dns_values[idx] if idx < len(dns_values) else "").strip(),
            }
        )

    if not rows or rows[-1]["subnet_prefix"] or rows[-1]["vlan_id"] or rows[-1]["gateway"] or rows[-1]["dns"]:
        rows.append({"subnet_prefix": "", "vlan_id": "", "gateway": "", "dns": ""})
    return rows


def register_admin_routes(app):
    @app.route("/admin", methods=["GET", "POST"])
    def admin():
        if not is_admin_session():
            return redirect(url_for("login"))

        error = None
        message = None
        subnet_vlan_rows = _build_subnet_vlan_rows()
        action = (request.form.get("action") or "").strip() if request.method == "POST" else ""
        delete_target_subnet = ""
        if action.startswith("delete_subnet_vlan|"):
            _, _, delete_target_subnet = action.partition("|")
            action = "delete_subnet_vlan"

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

        if request.method == "POST" and action == "save_subnet_vlan":
            try:
                rows = _build_rows_from_form(request.form)
                mapping_options = []
                gateway_options = []
                dns_options = []
                seen_subnets = set()

                for row in rows:
                    subnet_prefix = (row.get("subnet_prefix") or "").strip()
                    vlan_text = (row.get("vlan_id") or "").strip()
                    gateway_text = (row.get("gateway") or "").strip()
                    dns_text = (row.get("dns") or "").strip()

                    if not subnet_prefix and not vlan_text and not gateway_text and not dns_text:
                        continue

                    if not subnet_prefix:
                        raise RuntimeError("サブネットを入力してください。")
                    if not _is_valid_subnet_prefix(subnet_prefix):
                        raise RuntimeError(
                            f"サブネット形式が不正です: {subnet_prefix} (例: 192.168.10)"
                        )

                    if subnet_prefix in seen_subnets:
                        raise RuntimeError(f"サブネットが重複しています: {subnet_prefix}")
                    seen_subnets.add(subnet_prefix)

                    normalized_vlan, vlan_error = _normalize_vlan_text(vlan_text)
                    if vlan_error:
                        raise RuntimeError(f"{subnet_prefix}: {vlan_error}")

                    normalized_gateway, gateway_error = _normalize_gateway_text(gateway_text)
                    if gateway_error:
                        raise RuntimeError(f"{subnet_prefix}: {gateway_error}")

                    normalized_dns, dns_error = _normalize_dns_text(dns_text)
                    if dns_error:
                        raise RuntimeError(f"{subnet_prefix}: {dns_error}")

                    if normalized_vlan:
                        mapping_options.append((subnet_prefix, normalized_vlan))
                    if normalized_gateway:
                        gateway_options.append((subnet_prefix, normalized_gateway))
                    if normalized_dns:
                        dns_options.append((subnet_prefix, normalized_dns))

                active_subnets = _sort_subnet_prefixes(seen_subnets)
                db.replace_master_options(
                    "subnet",
                    [(subnet, subnet) for subnet in active_subnets],
                    updated_by=get_session_user_name(),
                )

                hidden_subnets = db.get_hidden_subnet_set()
                remaining_hidden = _sort_subnet_prefixes(
                    [subnet for subnet in hidden_subnets if subnet not in seen_subnets]
                )
                db.replace_master_options(
                    "subnet_hidden",
                    [(subnet, subnet) for subnet in remaining_hidden],
                    updated_by=get_session_user_name(),
                )

                CURRENT_SUBNET_OPTIONS.clear()
                CURRENT_SUBNET_OPTIONS.extend([(subnet, subnet) for subnet in active_subnets])

                db.replace_master_options(
                    "subnet_vlan",
                    mapping_options,
                    updated_by=get_session_user_name(),
                )
                db.replace_master_options(
                    "subnet_gateway",
                    gateway_options,
                    updated_by=get_session_user_name(),
                )
                db.replace_master_options(
                    "subnet_dns",
                    dns_options,
                    updated_by=get_session_user_name(),
                )
                subnet_vlan_rows = _build_subnet_vlan_rows()
                message = f"サブネットネットワーク設定を保存しました。登録件数: {len(seen_subnets)}"
            except Exception as exc:
                subnet_vlan_rows = _build_rows_from_form(request.form)
                error = str(exc)

        if request.method == "POST" and action == "delete_subnet_vlan":
            try:
                subnet_to_delete = (delete_target_subnet or request.form.get("delete_target_subnet") or "").strip()
                if not subnet_to_delete:
                    raise RuntimeError("削除対象のサブネットが指定されていません。")

                current_subnets = [
                    (value, label)
                    for value, label in db.get_master_options("subnet")
                    if (value or "").strip() != subnet_to_delete
                ]
                db.replace_master_options(
                    "subnet",
                    current_subnets,
                    updated_by=get_session_user_name(),
                )

                hidden_subnets = db.get_hidden_subnet_set()
                hidden_subnets.add(subnet_to_delete)
                db.replace_master_options(
                    "subnet_hidden",
                    [(subnet, subnet) for subnet in _sort_subnet_prefixes(hidden_subnets)],
                    updated_by=get_session_user_name(),
                )

                current_map = db.get_subnet_vlan_map()
                new_mapping = [(subnet, vlan) for subnet, vlan in current_map.items() if subnet != subnet_to_delete]
                current_gateway_map = db.get_subnet_gateway_map()
                new_gateway_mapping = [
                    (subnet, gateway) for subnet, gateway in current_gateway_map.items() if subnet != subnet_to_delete
                ]
                current_dns_map = db.get_subnet_dns_map()
                new_dns_mapping = [(subnet, dns) for subnet, dns in current_dns_map.items() if subnet != subnet_to_delete]

                db.replace_master_options(
                    "subnet_vlan",
                    new_mapping,
                    updated_by=get_session_user_name(),
                )
                db.replace_master_options(
                    "subnet_gateway",
                    new_gateway_mapping,
                    updated_by=get_session_user_name(),
                )
                db.replace_master_options(
                    "subnet_dns",
                    new_dns_mapping,
                    updated_by=get_session_user_name(),
                )
                CURRENT_SUBNET_OPTIONS.clear()
                CURRENT_SUBNET_OPTIONS.extend(
                    [(value, label) for value, label in current_subnets if (value or "").strip()]
                )
                subnet_vlan_rows = _build_subnet_vlan_rows()
                message = f"サブネット {subnet_to_delete} を削除し、除外リストへ登録しました。"
            except Exception as exc:
                subnet_vlan_rows = _build_subnet_vlan_rows()
                error = str(exc)

        if request.method == "POST" and action == "save_template_sysprep":
            try:
                names = request.form.getlist("template_sysprep_name")
                users = request.form.getlist("template_sysprep_user")
                passwords = request.form.getlist("template_sysprep_password")
                rows = []
                for idx, name in enumerate(names):
                    name = (name or "").strip()
                    if not name:
                        continue
                    user = (users[idx] if idx < len(users) else "").strip()
                    raw_password = (passwords[idx] if idx < len(passwords) else "").strip()
                    rows.append({
                        "template_name": name,
                        "os_user": user,
                        "os_password": raw_password,
                    })
                db.replace_template_sysprep_credentials(rows, updated_by=get_session_user_name())
                message = f"テンプレート認証情報を保存しました（{len(rows)} 件）。"
            except Exception as exc:
                error = str(exc)

        settings_display = dict(db.get_all_settings())
        for i in range(1, 4):
            if settings_display.get(f"hyperv_host{i}_password"):
                settings_display[f"hyperv_host{i}_password"] = ""

        # ホストから取得済みのテンプレート名と既存認証情報をマージ
        existing_creds = {
            row["template_name"]: row
            for row in db.get_template_sysprep_credentials()
        }
        known_template_names = [value for value, _ in CURRENT_VM_TEMPLATE_OPTIONS if value]
        template_sysprep_rows = []
        seen = set()
        for tname in known_template_names:
            seen.add(tname)
            cred = existing_creds.get(tname, {})
            template_sysprep_rows.append({
                "template_name": tname,
                "os_user": cred.get("os_user", ""),
                "os_password": cred.get("os_password", ""),
            })
        # 既存登録済みでホスト一覧にないテンプレートも残す
        for tname, cred in existing_creds.items():
            if tname not in seen:
                template_sysprep_rows.append(cred)
        # 末尾に空白行を追加
        template_sysprep_rows.append({"template_name": "", "os_user": "", "os_password": ""})

        return render_template(
            "admin.html",
            settings=settings_display,
            subnet_vlan_rows=subnet_vlan_rows,
            template_sysprep_rows=template_sysprep_rows,
            logs=db.get_recent_logs(20),
            message=message,
            error=error,
            user_name=get_session_user_name(),
        )
