from core import db

SESSION_API_KEY = "redmine_api_key"
SESSION_USER_NAME = "redmine_user_name"
SESSION_USER_ROLE = "user_role"
SESSION_IS_ADMIN = "is_admin"

CURRENT_ASSIGNEE_NAME_TO_ID = dict(db.get_assignee_name_to_id())
CURRENT_VHOST_IP_DISPLAY_MAP = dict(db.get_vhost_ip_display_map())

DEFAULT_VM_TEMPLATE_OPTIONS = list(db.get_master_options("vm_template"))
CURRENT_VM_TEMPLATE_OPTIONS = list(DEFAULT_VM_TEMPLATE_OPTIONS)

DEFAULT_VM_SWITCH_OPTIONS = list(db.get_master_options("vm_switch"))
CURRENT_VM_SWITCH_OPTIONS = list(DEFAULT_VM_SWITCH_OPTIONS)

DEFAULT_SUBNET_OPTIONS = list(db.get_visible_subnet_options())
CURRENT_SUBNET_OPTIONS = list(DEFAULT_SUBNET_OPTIONS)

DEFAULT_OS_OPTIONS = list(db.get_master_options("os"))
CURRENT_OS_OPTIONS = list(DEFAULT_OS_OPTIONS)

DEFAULT_USAGE_OPTIONS = list(db.get_master_options("usage"))
CURRENT_USAGE_OPTIONS = list(DEFAULT_USAGE_OPTIONS)
