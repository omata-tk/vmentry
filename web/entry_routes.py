import json
import ipaddress
import threading
import uuid

from flask import jsonify, redirect, render_template, request, url_for
from werkzeug.datastructures import MultiDict

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
    ("ip_assignment_mode", "IP割当方法"),
    ("manual_ip_address", "手動IPアドレス"),
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
    ("clone_host_ip", "複製先ホスト"),
    ("sysprep_enabled", "Sysprep 実行"),
    ("template_iso_path", "ISOイメージ（Sysprep後にマウント）"),
    ("vcpu_count", "仮想プロセッサ カウント"),
    ("enable_nested_virtualization", "入れ子になった仮想化"),
    ("startup_memory", "起動メモリ"),
    ("memory_unit", "メモリ単位"),
    ("use_dynamic_memory", "動的メモリ"),
    ("virtual_switch", "仮想スイッチ"),
    ("vlan_id", "VLAN ID"),
    ("manual_disks_json", "ストレージ構成"),
    ("os_install_mode", "OSインストール方法"),
    ("os_iso_path", "OS ISO パス"),
]


_ENTRY_JOB_LOCK = threading.Lock()
_ENTRY_JOBS = {}


def _build_entry_job_steps(ticket_only, sysprep_enabled):
    steps = []
    if not ticket_only:
        steps.append(("vm_precheck", "仮想マシン事前確認中"))

    steps.append(("ticket_create", "Redmineチケット作成中"))

    if not ticket_only:
        steps.append(("vm_clone", "仮想マシン複製中"))
        steps.append(("vm_verify", "仮想マシン存在確認中"))
        if sysprep_enabled:
            steps.extend(
                [
                    ("vm_boot", "仮想マシン起動中"),
                    ("sysprep", "Sysprep実行中"),
                    ("vm_restart", "仮想マシン再起動中"),
                    ("os_config", "OS設定中"),
                ]
            )

    steps.append(("completed", "完了"))
    return [{"code": code, "label": label, "status": "waiting"} for code, label in steps]


def _create_entry_job(owner, ticket_only, sysprep_enabled):
    job_id = str(uuid.uuid4())
    job = {
        "id": job_id,
        "owner": owner,
        "status": "running",
        "message": "処理を開始しています。",
        "stage_code": "",
        "steps": _build_entry_job_steps(ticket_only, sysprep_enabled),
        "result": None,
        "error": None,
    }
    with _ENTRY_JOB_LOCK:
        _ENTRY_JOBS[job_id] = job
    return job_id


def _get_entry_job(job_id):
    with _ENTRY_JOB_LOCK:
        job = _ENTRY_JOBS.get(job_id)
        if not job:
            return None
        return {
            "id": job["id"],
            "owner": job["owner"],
            "status": job["status"],
            "message": job["message"],
            "stage_code": job["stage_code"],
            "steps": [dict(step) for step in job["steps"]],
            "result": dict(job["result"]) if job.get("result") else None,
            "error": job.get("error"),
        }


def _update_entry_job(job_id, stage_code, message):
    with _ENTRY_JOB_LOCK:
        job = _ENTRY_JOBS.get(job_id)
        if not job:
            return

        job["stage_code"] = stage_code
        job["message"] = message
        reached = False
        for step in job["steps"]:
            if step["code"] == stage_code:
                step["status"] = "active"
                reached = True
            elif not reached:
                if step["status"] not in ("skipped", "error"):
                    step["status"] = "complete"
            else:
                if step["status"] not in ("skipped", "error"):
                    step["status"] = "waiting"


def _complete_entry_job(job_id, result, message):
    with _ENTRY_JOB_LOCK:
        job = _ENTRY_JOBS.get(job_id)
        if not job:
            return

        job["status"] = "success"
        job["stage_code"] = "completed"
        job["message"] = message
        job["result"] = dict(result)
        job["error"] = None
        for step in job["steps"]:
            step["status"] = "complete"


def _fail_entry_job(job_id, error_message):
    with _ENTRY_JOB_LOCK:
        job = _ENTRY_JOBS.get(job_id)
        if not job:
            return

        job["status"] = "error"
        job["message"] = error_message
        job["error"] = error_message
        active_step = None
        for step in job["steps"]:
            if step["status"] == "active":
                active_step = step
                break
        if active_step:
            active_step["status"] = "error"


def _perform_entry_create(form_data, api_key, user_name, ticket_data=None, progress_callback=None):
    if ticket_data is None:
        ticket_data, errors = build_ticket_data(form_data)
        if errors:
            raise RuntimeError(" ".join(errors))

    result = None
    try:
        project_id = redmine.get_redmine_project_id(db.get_setting("project_name", ""), api_key=api_key)
        if project_id is None:
            raise RuntimeError(
                f'プロジェクトが見つかりません: {db.get_setting("project_name", "")}'
            )

        confirmed_ip = (form_data.get("confirmed_ip") or "").strip()
        if not confirmed_ip:
            raise RuntimeError("確認画面の割当予定IPが取得できません。確認画面から再実行してください。")

        if ticket_data.get("ip_assignment_mode") == "manual":
            requested_ip = (ticket_data.get("manual_ip_address") or "").strip()
            if requested_ip != confirmed_ip:
                raise RuntimeError(
                    "確認画面の手動IPが変更されています。入力画面に戻って再確認してください。"
                )

        if redmine.is_ip_already_registered(project_id, confirmed_ip, api_key=api_key):
            raise RuntimeError(
                f"確認後に同一IP ({confirmed_ip}) が登録されました。入力画面に戻って再確認してください。"
            )

        clone_request = None
        if not ticket_data.get("ticket_only"):
            clone_request = {
                "deploy_type": ticket_data.get("vm_config", {}).get("deploy_type"),
                "vm_template": ticket_data.get("vm_config", {}).get("vm_template"),
                "vm_name": (form_data.get("vm_name") or "").strip(),
                "confirmed_ip": confirmed_ip,
                "target_subnet": ticket_data.get("target_subnet", ""),
                "vhost_ip": _normalize_hyperv_host_ip(form_data.get("vhost_ip") or ""),
                "clone_host_ip": _normalize_hyperv_host_ip(
                    ticket_data.get("vm_config", {}).get("clone_host_ip", "")
                ),
                "vlan_id": ticket_data.get("vm_config", {}).get("manual", {}).get("vlan_id", ""),
                "vlan_resolution_mode": ticket_data.get("vm_config", {}).get("vlan_resolution_mode", "none"),
                "template_iso_mount": ticket_data.get("vm_config", {}).get("template_iso_mount", False),
                "template_iso_path": ticket_data.get("vm_config", {}).get("template_iso_path", ""),
                "sysprep_enabled": ticket_data.get("vm_config", {}).get("sysprep_enabled", False),
                "os_user": (form_data.get("os_user") or "").strip(),
                "os_password": (form_data.get("os_password") or "").strip(),
                "hosts": _configured_hyperv_hosts(),
            }
            if progress_callback:
                progress_callback("vm_precheck", "仮想マシン作成前の確認をしています。")
            hyperv.precheck_virtual_machine(clone_request)

        if progress_callback:
            progress_callback("ticket_create", "Redmineチケットを作成しています。")

        result = redmine.create_redmine_ticket(
            project_id,
            ticket_data,
            confirmed_ip,
            api_key=api_key,
        )
        if result.get("result") != "success":
            raise RuntimeError(result.get("message", "チケット登録に失敗しました。"))

        result["url"] = result.get("url") or build_ticket_url(result.get("id"))
        result["subject"] = ticket_data.get("subject")
        result["vm_name"] = form_data.get("vm_name")
        result["target_subnet"] = ticket_data.get("target_subnet")

        if clone_request:
            hyperv.create_virtual_machine(clone_request, progress_callback=progress_callback)
            db.append_log(
                "entry",
                user_name,
                "info",
                (
                    f"vm clone success ticket_id={result.get('id')} "
                    f"vm_name={clone_request['vm_name']} host={clone_request['clone_host_ip']} "
                    f"template={clone_request['vm_template']} vlan={clone_request['vlan_id'] or '-'} "
                    f"vlan_mode={clone_request.get('vlan_resolution_mode', 'none')} "
                    f"sysprep={'on' if clone_request.get('sysprep_enabled') else 'off'}"
                ),
            )

        db.append_log(
            "entry",
            user_name,
            "info",
            f'create success ticket_id={result.get("id")} vm_name={result.get("vm_name")} subnet={result.get("target_subnet")} ip={confirmed_ip}',
        )
        return result
    except Exception as exc:
        error = str(exc)
        stage_code = ""
        if "stage=" in error:
            try:
                stage_code = error.split("stage=", 1)[1].split()[0]
            except Exception:
                stage_code = ""
        if result and result.get("result") == "success":
            error = (
                f"チケットID {result.get('id')} は作成しましたが、"
                f"VM複製に失敗しました: {error}"
            )
        db.append_log(
            "entry",
            user_name,
            "error",
            (
                f"create exception: {error} "
                f"ticket_id={result.get('id') if result else '-'} "
                f"stage={stage_code or '-'} "
                f"vm_name={(form_data.get('vm_name') or '').strip() or '-'} "
                f"host={_normalize_hyperv_host_ip(form_data.get('vhost_ip') or '') or '-'}"
            ),
        )
        raise RuntimeError(error) from exc


def _run_entry_create_job(job_id, form_data, api_key, user_name):
    try:
        result = _perform_entry_create(
            form_data,
            api_key,
            user_name,
            progress_callback=lambda stage_code, message: _update_entry_job(job_id, stage_code, message),
        )
        _complete_entry_job(job_id, result, "処理が完了しました。")
    except Exception as exc:
        _fail_entry_job(job_id, str(exc))


def build_visible_confirm_fields(values):
    deploy_type = (values.get("deploy_type") or "").strip()
    ip_assignment_mode = _normalize_ip_assignment_mode(values.get("ip_assignment_mode") or "")
    template_iso_mount = (values.get("template_iso_mount") or "").strip() == "on"
    os_install_mode = (values.get("os_install_mode") or "").strip()

    _raw_ticket_only = values.get("ticket_only")
    ticket_only = _raw_ticket_only is True or (isinstance(_raw_ticket_only, str) and _raw_ticket_only.strip() == "on")

    visible_fields = []
    for field_key, field_label in CONFIRM_FIELDS:
        if field_key == "manual_ip_address" and ip_assignment_mode != "manual":
            continue
        if field_key == "target_subnet" and ip_assignment_mode == "manual":
            continue

        if ticket_only and field_key in (
            "vm_name",
            "vhost_ip",
            "vm_template",
            "clone_host_ip",
            "template_iso_path",
            "sysprep_enabled",
            "vcpu_count",
            "enable_nested_virtualization",
            "startup_memory",
            "memory_unit",
            "use_dynamic_memory",
            "virtual_switch",
            "vlan_id",
            "manual_disks_json",
            "os_install_mode",
            "os_iso_path",
        ):
            continue

        if field_key in (
            "vm_template",
            "clone_host_ip",
            "template_iso_path",
            "sysprep_enabled",
        ) and deploy_type != "template":
            continue

        if field_key == "template_iso_path" and not template_iso_mount:
            continue

        if field_key in (
            "vcpu_count",
            "enable_nested_virtualization",
            "startup_memory",
            "memory_unit",
            "use_dynamic_memory",
            "virtual_switch",
            "manual_disks_json",
            "os_install_mode",
            "os_iso_path",
        ) and deploy_type != "manual":
            continue

        if field_key == "os_iso_path" and os_install_mode != "iso":
            continue

        visible_fields.append((field_key, field_label))

    return visible_fields


def form_defaults():
    target_default = CURRENT_SUBNET_OPTIONS[0][0] if CURRENT_SUBNET_OPTIONS else ""
    clone_host_default = _configured_hyperv_host_options()[0][0] if _configured_hyperv_host_options() else ""
    return {
        "ip_assignment_mode": "auto",
        "manual_ip_address": "",
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
        "clone_host_ip": clone_host_default,
        "template_iso_mount": "",
        "template_iso_path": "",
        "sysprep_enabled": "",
        "vcpu_count": "",
        "enable_nested_virtualization": "",
        "startup_memory": "",
        "memory_unit": "GB",
        "use_dynamic_memory": "",
        "virtual_switch": "",
        "vlan_id": "",
        "manual_disks_json": "",
        "os_install_mode": "later",
        "os_iso_path": "",
        "ticket_only": "",
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

    if _normalize_ip_assignment_mode(values.get("ip_assignment_mode")) == "manual":
        manual_ip = (values.get("manual_ip_address") or "").strip()
        derived_subnet = _extract_subnet_prefix(manual_ip)
        if derived_subnet:
            values["target_subnet"] = derived_subnet

    return values


def build_confirm_display_values(values):
    display_values = dict(values)
    ip_assignment_mode = (values.get("ip_assignment_mode") or "").strip()
    if ip_assignment_mode == "manual":
        display_values["ip_assignment_mode"] = "手動入力"
    else:
        display_values["ip_assignment_mode"] = "対象サブネットから自動採番"

    display_values["manual_ip_address"] = values.get("manual_ip_address", "").strip() or "-"

    os_label_map = {value: label for value, label in CURRENT_OS_OPTIONS}
    vhost_label_map = dict(CURRENT_VHOST_IP_DISPLAY_MAP)
    template_label_map = {value: label for value, label in CURRENT_VM_TEMPLATE_OPTIONS}
    clone_host_label_map = {value: label for value, label in _configured_hyperv_host_options()}
    vm_switch_label_map = {value: label for value, label in CURRENT_VM_SWITCH_OPTIONS}
    display_values["os_value"] = os_label_map.get(values.get("os_value", ""), values.get("os_value", ""))
    display_values["vhost_ip"] = vhost_label_map.get(values.get("vhost_ip", ""), values.get("vhost_ip", ""))
    display_values["vm_template"] = template_label_map.get(values.get("vm_template", ""), values.get("vm_template", ""))
    display_values["clone_host_ip"] = clone_host_label_map.get(
        values.get("clone_host_ip", ""), values.get("clone_host_ip", "")
    )
    display_values["virtual_switch"] = vm_switch_label_map.get(
        values.get("virtual_switch", ""), values.get("virtual_switch", "")
    )
    effective_vlan_id, vlan_mode = _resolve_effective_vlan_id(
        values.get("target_subnet", ""),
        values.get("vlan_id", ""),
        values.get("deploy_type", ""),
    )
    if effective_vlan_id:
        if vlan_mode == "manual":
            display_values["vlan_id"] = f"{effective_vlan_id} (手動指定)"
        elif vlan_mode == "subnet":
            display_values["vlan_id"] = f"{effective_vlan_id} (対象サブネットから自動設定)"
        else:
            display_values["vlan_id"] = effective_vlan_id
    else:
        display_values["vlan_id"] = "-"
    display_values["enable_nested_virtualization"] = "有効" if values.get("enable_nested_virtualization") == "on" else "無効"
    display_values["use_dynamic_memory"] = "有効" if values.get("use_dynamic_memory") == "on" else "無効"
    display_values["sysprep_enabled"] = "実行する" if values.get("sysprep_enabled") == "on" else "実行しない"
    display_values["os_install_mode"] = (
        "後でオペレーティングシステムをインストールする"
        if values.get("os_install_mode") == "later"
        else "画像ファイル（.iso）からオペレーティングシステムをインストールする"
    )

    startup_memory = (values.get("startup_memory") or "").strip()
    memory_unit = (values.get("memory_unit") or "").strip()
    if startup_memory:
        display_values["startup_memory"] = f"{startup_memory} {memory_unit}".strip()

    display_values["template_iso_path"] = values.get("template_iso_path", "").strip() or "-"
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


def _is_valid_ipv4_address(value):
    text = (value or "").strip()
    if not text:
        return False
    try:
        ipaddress.IPv4Address(text)
        return True
    except ipaddress.AddressValueError:
        return False


def _normalize_ip_assignment_mode(value):
    text = (value or "").strip().lower()
    return "manual" if text == "manual" else "auto"


def _extract_subnet_prefix(ip_text):
    if not _is_valid_ipv4_address(ip_text):
        return ""
    parts = ip_text.strip().split(".")
    return ".".join(parts[:3])


def _is_disallowed_host_octet(ip_text):
    if not _is_valid_ipv4_address(ip_text):
        return False
    try:
        octet = int(ip_text.strip().split(".")[3])
    except (IndexError, ValueError):
        return False
    return octet in {0, 1, 255}


def _validate_vlan_id_text(vlan_text):
    text = (vlan_text or "").strip()
    if not text:
        return "", None
    try:
        vlan_int = int(text)
    except ValueError:
        return "", "VLAN IDは数値で入力してください。"
    if vlan_int < 1 or vlan_int > 4094:
        return "", "VLAN IDは1から4094の範囲で入力してください。"
    return str(vlan_int), None


def _resolve_effective_vlan_id(target_subnet, manual_vlan_id, deploy_type):
    manual_vlan, _ = _validate_vlan_id_text(manual_vlan_id)
    if manual_vlan:
        return manual_vlan, "manual"

    if (deploy_type or "").strip() != "template":
        return "", "none"

    mapped_vlan = db.get_vlan_id_for_subnet(target_subnet)
    mapped_vlan, _ = _validate_vlan_id_text(mapped_vlan)
    if mapped_vlan:
        return mapped_vlan, "subnet"
    return "", "none"


def build_ticket_data(form_data):
    errors = []
    selected_subnets = {value for value, _ in CURRENT_SUBNET_OPTIONS}

    ticket_only = (form_data.get("ticket_only") or "").strip() == "on"

    ip_assignment_mode = _normalize_ip_assignment_mode(form_data.get("ip_assignment_mode") or "")
    manual_ip_address = (form_data.get("manual_ip_address") or "").strip()
    target_subnet = (form_data.get("target_subnet") or "").strip()
    subject = (form_data.get("subject") or "").strip()
    vm_name = (form_data.get("vm_name") or "").strip()

    if ip_assignment_mode != "manual":
        if not target_subnet:
            errors.append("対象サブネットを選択してください。")
        elif selected_subnets and target_subnet not in selected_subnets:
            errors.append("対象サブネットは候補から選択してください。")

    if not subject:
        errors.append("Redmineチケット名は必須です。")

    if ip_assignment_mode == "manual":
        if not manual_ip_address:
            errors.append("IP割当方法で手動入力を選択した場合は手動IPアドレスを入力してください。")
        elif not _is_valid_ipv4_address(manual_ip_address):
            errors.append("手動IPアドレスはIPv4形式で入力してください。")
        elif _is_disallowed_host_octet(manual_ip_address):
            errors.append("手動IPアドレスの第4オクテットに 0/1/255 は使用できません。")
        else:
            target_subnet = _extract_subnet_prefix(manual_ip_address)

    if not ticket_only and not vm_name:
        errors.append("仮想マシン名は必須です。")
    elif not ticket_only:
        vm_name_error = hyperv.validate_vm_name(vm_name)
        if vm_name_error:
            errors.append(vm_name_error)

    deploy_type = (form_data.get("deploy_type") or "").strip()
    vm_template = (form_data.get("vm_template") or "").strip()
    clone_host_ip = (form_data.get("clone_host_ip") or "").strip()
    template_iso_mount = (form_data.get("template_iso_mount") or "").strip() == "on"
    template_iso_path = (form_data.get("template_iso_path") or "").strip()
    sysprep_enabled = (form_data.get("sysprep_enabled") or "").strip() == "on"
    vm_host = (form_data.get("vhost_ip") or "").strip()
    os_user = (form_data.get("os_user") or "").strip()
    os_password = (form_data.get("os_password") or "").strip()

    vcpu_count = (form_data.get("vcpu_count") or "").strip()
    startup_memory = (form_data.get("startup_memory") or "").strip()
    memory_unit = (form_data.get("memory_unit") or "").strip()
    virtual_switch = (form_data.get("virtual_switch") or "").strip()
    vlan_id = (form_data.get("vlan_id") or "").strip()
    os_install_mode = (form_data.get("os_install_mode") or "").strip() or "later"
    os_iso_path = (form_data.get("os_iso_path") or "").strip()
    manual_disks_raw = (form_data.get("manual_disks_json") or "").strip()

    if deploy_type and deploy_type not in ("template", "manual"):
        errors.append("作成方法が不正です。")

    if not ticket_only:
        if deploy_type == "template":
            configured_host_ips = _configured_hyperv_host_ips()
            if not vm_template:
                errors.append("VMテンプレートを選択してください。")
            if not clone_host_ip:
                errors.append("複製先ホストを選択してください。")
            if template_iso_mount and not template_iso_path:
                errors.append("ISOイメージをマウントする場合はパスを入力してください。")
            if not vm_host:
                errors.append("IPアドレス（仮想ホスト）を選択してください。")
            elif configured_host_ips and _normalize_hyperv_host_ip(clone_host_ip) not in configured_host_ips:
                errors.append("複製先ホストは管理者設定のホストIPから選択してください。")
            if sysprep_enabled:
                pass  # Sysprep 認証情報は管理者設定のテンプレート Sysprep 認証情報を使用するため、フォーム入力チェック不要
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

    normalized_vlan_id = ""
    vlan_resolution_mode = "none"
    effective_vlan_id = ""
    if not ticket_only:
        normalized_vlan_id, vlan_error = _validate_vlan_id_text(vlan_id)
        if vlan_error:
            errors.append(vlan_error)

        effective_vlan_id, vlan_resolution_mode = _resolve_effective_vlan_id(
            target_subnet,
            normalized_vlan_id,
            deploy_type,
        )

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
        "ip_assignment_mode": ip_assignment_mode,
        "manual_ip_address": manual_ip_address,
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
            "clone_host_ip": clone_host_ip,
            "template_iso_mount": template_iso_mount,
            "template_iso_path": template_iso_path,
            "sysprep_enabled": sysprep_enabled,
            "manual": {
                "vcpu_count": vcpu_count,
                "enable_nested_virtualization": (form_data.get("enable_nested_virtualization") or "").strip() == "on",
                "startup_memory": startup_memory,
                "memory_unit": memory_unit,
                "use_dynamic_memory": (form_data.get("use_dynamic_memory") or "").strip() == "on",
                "virtual_switch": virtual_switch,
                "vlan_id": effective_vlan_id,
                "disks": parse_manual_disks_json(manual_disks_raw)[0],
                "os_install_mode": os_install_mode,
                "os_iso_path": os_iso_path,
            },
            "vlan_resolution_mode": vlan_resolution_mode,
        },
        "ticket_only": ticket_only,
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


def _normalize_hyperv_host_ip(value):
    text = (value or "").strip()
    if not text:
        return ""

    text = text.replace("（Hyper-v）", "").replace("(Hyper-v)", "")
    text = text.replace("（Hyper-V）", "").replace("(Hyper-V)", "")
    return text.strip()


def _configured_hyperv_host_ips():
    ips = set()
    for host in _configured_hyperv_hosts():
        ip = _normalize_hyperv_host_ip(host.get("ip") or "")
        if ip:
            ips.add(ip)
    return ips


def _configured_hyperv_host_options():
    options = []
    for host in _configured_hyperv_hosts():
        ip = _normalize_hyperv_host_ip(host.get("ip") or "")
        if not ip:
            continue
        index = host.get("index")
        label = f"ホスト{index}" if index else "Hyper-V ホスト"
        options.append((ip, f"{label} ({ip})"))
    return options


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
    @app.route("/entry/create/start", methods=["POST"])
    def entry_create_start():
        if not (get_session_api_key() or is_admin_session()):
            return jsonify({"ok": False, "message": "認証が必要です。"}), 401

        api_key = get_session_api_key()
        if not api_key:
            return jsonify({"ok": False, "message": "APIキーが未設定です。"}), 400

        form_data = MultiDict(request.form)
        ticket_only = (form_data.get("ticket_only") or "").strip() == "on"
        sysprep_enabled = (form_data.get("sysprep_enabled") or "").strip() == "on"
        user_name = get_session_user_name()
        job_id = _create_entry_job(user_name, ticket_only, sysprep_enabled)

        worker = threading.Thread(
            target=_run_entry_create_job,
            args=(job_id, form_data, api_key, user_name),
            daemon=True,
        )
        worker.start()

        job = _get_entry_job(job_id)
        return jsonify(
            {
                "ok": True,
                "job": {
                    "id": job["id"],
                    "status": job["status"],
                    "message": job["message"],
                    "stage_code": job["stage_code"],
                    "steps": job["steps"],
                    "redirect_url": url_for("entry", job_id=job_id),
                },
            }
        )

    @app.route("/entry/create/status/<job_id>", methods=["GET"])
    def entry_create_status(job_id):
        if not (get_session_api_key() or is_admin_session()):
            return jsonify({"ok": False, "message": "認証が必要です。"}), 401

        job = _get_entry_job(job_id)
        if not job or job.get("owner") != get_session_user_name():
            return jsonify({"ok": False, "message": "処理状況が見つかりません。"}), 404

        return jsonify(
            {
                "ok": True,
                "job": {
                    "id": job["id"],
                    "status": job["status"],
                    "message": job["message"],
                    "stage_code": job["stage_code"],
                    "steps": job["steps"],
                    "result": job.get("result"),
                    "error": job.get("error"),
                    "redirect_url": url_for("entry", job_id=job_id),
                },
            }
        )

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

        job_id = (request.args.get("job_id") or "").strip()
        form_data = request.form if request.method == "POST" else {}
        values = build_values(form_data)
        error = None
        result = None
        notice = None
        confirm = False
        confirmed_ip = ""
        action = (form_data.get("action") or "").strip()
        confirm_display_values = build_confirm_display_values(values)

        if request.method == "GET" and job_id:
            job = _get_entry_job(job_id)
            if job and job.get("owner") == get_session_user_name():
                if job.get("status") == "success":
                    result = job.get("result")
                    notice = "前回の作成処理が完了しました。"
                elif job.get("status") == "error":
                    error = job.get("error") or "処理に失敗しました。"
                    notice = "前回の作成処理結果を表示しています。"

        if request.method == "POST" and action in ("confirm", "create"):
            selected_subnets = {value for value, _ in CURRENT_SUBNET_OPTIONS}
            if (
                _normalize_ip_assignment_mode(values.get("ip_assignment_mode")) != "manual"
                and values.get("target_subnet") not in selected_subnets
                and CURRENT_SUBNET_OPTIONS
            ):
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

                    if ticket_data.get("ip_assignment_mode") == "manual":
                        confirmed_ip = ticket_data.get("manual_ip_address", "").strip()
                        if redmine.is_ip_already_registered(project_id, confirmed_ip, api_key=api_key):
                            raise RuntimeError(
                                f"指定されたIP ({confirmed_ip}) は既に登録済みです。"
                                "別のIPを入力してください。"
                            )
                    else:
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
                    result = _perform_entry_create(
                        form_data,
                        api_key,
                        get_session_user_name(),
                        ticket_data=ticket_data,
                    )
                except Exception as exc:
                    error = str(exc)

        return render_template(
            "entry.html",
            values=values,
            confirm_display_values=confirm_display_values,
            confirm=confirm,
            confirmed_ip=confirmed_ip,
            confirm_fields=build_visible_confirm_fields(values),
            error=error,
            result=result,
            notice=notice,
            user_name=get_session_user_name(),
            is_admin=is_admin_session(),
            subnet_options=CURRENT_SUBNET_OPTIONS,
            vhost_options=sorted(CURRENT_VHOST_IP_DISPLAY_MAP.items(), key=lambda item: item[1]),
            clone_host_options=_configured_hyperv_host_options(),
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

        requested_host_ip = _normalize_hyperv_host_ip(request.args.get("host_ip") or "")
        configured_hosts = _configured_hyperv_hosts()
        if requested_host_ip:
            browse_hosts = [
                host
                for host in configured_hosts
                if _normalize_hyperv_host_ip(host.get("ip") or "") == requested_host_ip
            ]
            if not browse_hosts:
                return jsonify(
                    {
                        "ok": False,
                        "message": f"指定されたホストが管理設定に見つかりません: {requested_host_ip}",
                    }
                ), 400
        else:
            browse_hosts = configured_hosts

        host_errors = []
        any_host_configured = False

        for host in browse_hosts:
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
            if requested_host_ip:
                message = f"指定ホスト {requested_host_ip} の接続情報が不足しています。"
            else:
                message = "管理者設定のホスト1〜3に接続情報がありません。"
        else:
            message = "ホストサーバに接続できませんでした。"
            if host_errors:
                message = f"{message} " + " ".join(host_errors)

        return jsonify({"ok": False, "message": message}), 503
