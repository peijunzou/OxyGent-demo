import re

ALLOWED_ACTIONS = {"note", "xingyun_tag_check", "changan_workorder_check", "shell"}
DEFAULT_DUE_TIME = "09:00"
VALID_WEEKDAYS = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}
VALID_SCHEDULE_KINDS = {"daily", "weekly", "interval"}
TOOL_NAMES = {
    "add_todo",
    "add_schedule",
    "update_todo",
    "close_todo",
    "query_todos",
    "get_current_time",
}
ID_PATTERN = re.compile(r"\b(?:todo|schedule)-\d{8,}\b")
