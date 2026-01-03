import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from oxygent import oxy
from oxygent.schemas import OxyRequest
from pydantic import Field

from .constants import ALLOWED_ACTIONS, DEFAULT_DUE_TIME, ID_PATTERN, VALID_SCHEDULE_KINDS, VALID_WEEKDAYS
from .guards import validate_action_requirements
from .memory import clear_candidates, get_memory_key, set_candidates
from .store import TASKS_PATH, TODOS_PATH, ensure_tasks, ensure_todos, save_json


todo_fh = oxy.FunctionHub(name="todo_tools")
time_fh = oxy.FunctionHub(name="time_tools")


def validate_due_at(value: str) -> Optional[str]:
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            datetime.strptime(value, fmt)
            return value
        except ValueError:
            continue
    return None


def normalize_time(value: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    if not value:
        return None, None
    raw = value.strip()
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", raw)
    if not match:
        return None, "时间格式错误，请使用 HH:MM。"
    hour = int(match.group(1))
    minute = int(match.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None, "时间格式错误，请使用 HH:MM。"
    return f"{hour:02d}:{minute:02d}", None


def normalize_due_at(value: str) -> Tuple[Optional[str], Optional[str]]:
    if not value:
        return None, "缺少执行时间。"
    now = datetime.now()
    parsed = validate_due_at(value.strip())
    if not parsed:
        return None, "执行时间格式错误，请使用 YYYY-MM-DD HH:MM。"
    parsed_dt = datetime.strptime(parsed[:16], "%Y-%m-%d %H:%M")
    if parsed_dt < now - timedelta(minutes=1):
        return None, "执行时间早于当前时间，请确认。"
    return parsed_dt.strftime("%Y-%m-%d %H:%M"), None


def build_action(
    action_type: str,
    message: Optional[str],
    repo_path: Optional[str],
    test_mode: Optional[bool],
    command: Optional[str],
    workdir: Optional[str],
    args: Optional[str],
) -> Dict[str, Any]:
    action: Dict[str, Any] = {"type": action_type}
    if action_type == "note":
        action["message"] = message or "待办事项"
    elif action_type == "xingyun_tag_check":
        if repo_path:
            action["repo_path"] = repo_path
        if test_mode is not None:
            action["test_mode"] = test_mode
    elif action_type == "changan_workorder_check":
        if repo_path:
            action["repo_path"] = repo_path
        if test_mode is not None:
            action["test_mode"] = test_mode
    elif action_type == "shell":
        if command:
            action["command"] = command
        if workdir:
            action["workdir"] = workdir
        if args:
            action["args"] = [item.strip() for item in args.split(",") if item.strip()]
    return action


def format_schedule_label(schedule: Dict[str, Any]) -> str:
    kind = schedule.get("kind")
    if kind == "daily":
        return f"每天 {schedule.get('time', DEFAULT_DUE_TIME)}"
    if kind == "weekly":
        day_map = {
            "mon": "周一",
            "tue": "周二",
            "wed": "周三",
            "thu": "周四",
            "fri": "周五",
            "sat": "周六",
            "sun": "周日",
        }
        day_label = day_map.get(schedule.get("day_of_week", ""), "周-")
        return f"{day_label} {schedule.get('time', DEFAULT_DUE_TIME)}"
    if kind == "interval":
        return f"每隔 {schedule.get('minutes', '-')} 分钟"
    return "未知排程"


def create_schedule_task(title: str, schedule: Dict[str, Any], action: Dict[str, Any]) -> str:
    tasks = ensure_tasks()
    task_id = f"schedule-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    task_item = {
        "id": task_id,
        "type": "todo_create",
        "schedule": schedule,
        "enabled": True,
        "created_at": datetime.now().isoformat(),
        "todo": {
            "title": title,
            "action": action,
        },
    }
    tasks.append(task_item)
    save_json(TASKS_PATH, tasks)
    return f"已新增重复任务：{title}，{format_schedule_label(schedule)}"


def _parse_id_list(value: Optional[str]) -> List[str]:
    if not value:
        return []
    ids = ID_PATTERN.findall(value)
    if ids:
        return ids
    raw = value.strip()
    return [raw] if raw else []


def _find_todo(todos: List[Dict[str, Any]], todo_id: Optional[str], title: Optional[str]) -> Tuple[Optional[int], Optional[str]]:
    if todo_id:
        for idx, todo in enumerate(todos):
            if todo.get("id") == todo_id:
                return idx, None
        return None, f"未找到 id 为 {todo_id} 的代办。"

    if title:
        matches = [idx for idx, todo in enumerate(todos) if todo.get("title") == title]
        if not matches:
            return None, f"未找到标题为「{title}」的代办。"
        if len(matches) > 1:
            ids = [todos[idx].get("id") for idx in matches if todos[idx].get("id")]
            return None, f"标题重复，请提供 id。可选 id：{', '.join(ids)}"
        return matches[0], None

    return None, "需要提供 todo_id 或 title 用于定位代办。"


def _find_schedule(tasks: List[Dict[str, Any]], task_id: Optional[str], title: Optional[str]) -> Tuple[Optional[int], Optional[str]]:
    if task_id:
        for idx, task in enumerate(tasks):
            if task.get("id") == task_id:
                return idx, None
        return None, f"未找到 id 为 {task_id} 的排程。"

    if title:
        matches = [
            idx
            for idx, task in enumerate(tasks)
            if task.get("type") == "todo_create" and (task.get("todo") or {}).get("title") == title
        ]
        if not matches:
            return None, f"未找到标题为「{title}」的排程。"
        if len(matches) > 1:
            ids = [tasks[idx].get("id") for idx in matches if tasks[idx].get("id")]
            return None, f"标题重复，请提供 id。可选 id：{', '.join(ids)}"
        return matches[0], None

    return None, "需要提供 todo_id 或 title 用于定位排程。"


@todo_fh.tool(description="新增一次性代办任务，时间需为明确日期")
def add_todo(
    title: str = Field(description="代办标题"),
    due_at: str = Field(description="执行时间，格式 YYYY-MM-DD HH:MM"),
    action_type: str = Field(description="动作类型：note / xingyun_tag_check / changan_workorder_check / shell", default="note"),
    action_message: Optional[str] = Field(description="note 类型的说明", default=None),
    repo_path: Optional[str] = Field(description="xingyun_tag_check 的仓库路径", default=None),
    test_mode: Optional[bool] = Field(description="xingyun_tag_check 是否测试模式", default=None),
    command: Optional[str] = Field(description="shell 命令", default=None),
    workdir: Optional[str] = Field(description="shell 工作目录", default=None),
    args: Optional[str] = Field(description="shell 参数，逗号分隔", default=None),
) -> str:
    if action_type not in ALLOWED_ACTIONS:
        return f"不支持的 action_type：{action_type}"
    requirement_error = validate_action_requirements(action_type, repo_path, command)
    if requirement_error:
        return requirement_error
    due_value, err = normalize_due_at(due_at)
    if err:
        return err

    action = build_action(action_type, action_message or title, repo_path, test_mode, command, workdir, args)
    todos = ensure_todos()
    todo_id = f"todo-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    todo_item = {
        "id": todo_id,
        "title": title,
        "due_at": due_value,
        "status": "open",
        "action": action,
        "created_at": datetime.now().isoformat(),
    }
    todos.append(todo_item)
    save_json(TODOS_PATH, todos)
    return f"已新增代办：{title}，执行时间 {due_value}"


@todo_fh.tool(description="新增重复排程代办任务")
def add_schedule(
    title: str = Field(description="代办标题"),
    schedule_kind: str = Field(description="排程类型：daily / weekly / interval"),
    time: Optional[str] = Field(description="执行时间 HH:MM（daily/weekly 需要）", default=None),
    day_of_week: Optional[str] = Field(description="每周几：mon..sun（weekly 需要）", default=None),
    interval_minutes: Optional[int] = Field(description="间隔分钟（interval 需要）", default=None),
    action_type: str = Field(description="动作类型：note / xingyun_tag_check / changan_workorder_check / shell", default="note"),
    action_message: Optional[str] = Field(description="note 类型的说明", default=None),
    repo_path: Optional[str] = Field(description="xingyun_tag_check 的仓库路径", default=None),
    test_mode: Optional[bool] = Field(description="xingyun_tag_check 是否测试模式", default=None),
    command: Optional[str] = Field(description="shell 命令", default=None),
    workdir: Optional[str] = Field(description="shell 工作目录", default=None),
    args: Optional[str] = Field(description="shell 参数，逗号分隔", default=None),
) -> str:
    if action_type not in ALLOWED_ACTIONS:
        return f"不支持的 action_type：{action_type}"
    if schedule_kind not in VALID_SCHEDULE_KINDS:
        return f"不支持的 schedule_kind：{schedule_kind}"
    requirement_error = validate_action_requirements(action_type, repo_path, command)
    if requirement_error:
        return requirement_error

    schedule: Dict[str, Any] = {"kind": schedule_kind}
    if schedule_kind == "daily":
        if not time:
            return "daily 排程需要提供 time。"
        time_value, err = normalize_time(time)
        if err:
            return err
        schedule["time"] = time_value
    elif schedule_kind == "weekly":
        if not day_of_week:
            return "weekly 排程需要提供 day_of_week。"
        if not time:
            return "weekly 排程需要提供 time。"
        day = day_of_week.strip().lower()
        if day not in VALID_WEEKDAYS:
            return "day_of_week 需为 mon..sun。"
        time_value, err = normalize_time(time)
        if err:
            return err
        schedule["day_of_week"] = day
        schedule["time"] = time_value
    elif schedule_kind == "interval":
        if interval_minutes is None:
            return "interval 排程需要提供 interval_minutes。"
        try:
            minutes = int(interval_minutes)
        except (TypeError, ValueError):
            return "interval_minutes 需为整数。"
        if minutes <= 0:
            return "interval_minutes 需要大于 0。"
        schedule["minutes"] = minutes

    action = build_action(action_type, action_message or title, repo_path, test_mode, command, workdir, args)
    return create_schedule_task(title, schedule, action)


@todo_fh.tool(description="修改业务代办任务的标题或时间")
def update_todo(
    todo_id: Optional[str] = Field(description="代办 id", default=None),
    title: Optional[str] = Field(description="原标题，用于查找代办", default=None),
    new_title: Optional[str] = Field(description="新的标题", default=None),
    new_due_at: Optional[str] = Field(description="新的执行时间，格式 YYYY-MM-DD HH:MM", default=None),
    new_action_type: Optional[str] = Field(description="新的动作类型", default=None),
    new_action_message: Optional[str] = Field(description="新的动作说明", default=None),
    repo_path: Optional[str] = Field(description="更新 xingyun_tag_check 的仓库路径", default=None),
    test_mode: Optional[bool] = Field(description="更新 xingyun_tag_check 的测试模式", default=None),
    command: Optional[str] = Field(description="更新 shell 命令", default=None),
    workdir: Optional[str] = Field(description="更新 shell 工作目录", default=None),
    args: Optional[str] = Field(description="更新 shell 参数，逗号分隔", default=None),
    oxy_request: OxyRequest = None,
) -> str:
    todos = ensure_todos()
    tasks = ensure_tasks()
    idx, err = _find_todo(todos, todo_id, title)
    if err:
        schedule_idx, schedule_err = _find_schedule(tasks, todo_id, title)
        if schedule_idx is not None:
            return "排程任务暂不支持修改，请提供代办标题或 id。"
        return err

    todo = todos[idx]
    if todo.get("status") == "done":
        return "该代办已完成，无法修改。"

    changes = []
    if new_title:
        todo["title"] = new_title
        changes.append("标题")
    if new_due_at:
        due_value, err = normalize_due_at(new_due_at)
        if err:
            return err
        todo["due_at"] = due_value
        changes.append("执行时间")
    if new_action_type:
        if new_action_type not in ALLOWED_ACTIONS:
            return f"不支持的 new_action_type：{new_action_type}"
        requirement_error = validate_action_requirements(new_action_type, repo_path, command)
        if requirement_error:
            return requirement_error
        todo["action"] = build_action(
            new_action_type,
            new_action_message or todo.get("title"),
            repo_path,
            test_mode,
            command,
            workdir,
            args,
        )
        changes.append("动作")
    elif any([new_action_message, repo_path, test_mode is not None, command, workdir, args]):
        current_type = todo.get("action", {}).get("type", "note")
        requirement_error = validate_action_requirements(current_type, repo_path, command)
        if requirement_error:
            return requirement_error
        todo["action"] = build_action(
            current_type,
            new_action_message or todo.get("title"),
            repo_path,
            test_mode,
            command,
            workdir,
            args,
        )
        changes.append("动作")

    if not changes:
        return "未检测到可更新的字段。"

    todo["updated_at"] = datetime.now().isoformat()
    save_json(TODOS_PATH, todos)
    if oxy_request:
        clear_candidates(get_memory_key(oxy_request))
    return f"已更新代办：{todo.get('title')}（{', '.join(changes)}）"


@todo_fh.tool(description="关闭业务代办任务")
def close_todo(
    todo_id: Optional[str] = Field(description="代办 id", default=None),
    title: Optional[str] = Field(description="原标题，用于查找代办", default=None),
    close_note: Optional[str] = Field(description="关闭说明", default=None),
    oxy_request: OxyRequest = None,
) -> str:
    todos = ensure_todos()
    tasks = ensure_tasks()
    id_list = _parse_id_list(todo_id)
    if id_list:
        closed = []
        skipped = []
        for item_id in id_list:
            if item_id.startswith("schedule-"):
                idx, err = _find_schedule(tasks, item_id, None)
                if err:
                    skipped.append(item_id)
                    continue
                task = tasks[idx]
                if not task.get("enabled", True):
                    skipped.append(item_id)
                    continue
                task["enabled"] = False
                task["disabled_at"] = datetime.now().isoformat()
                title_value = (task.get("todo") or {}).get("title") or task.get("id", "task")
                closed.append(f"{item_id}（{title_value}）")
            else:
                idx, err = _find_todo(todos, item_id, None)
                if err:
                    skipped.append(item_id)
                    continue
                todo = todos[idx]
                if todo.get("status") == "done":
                    skipped.append(item_id)
                    continue
                todo["status"] = "done"
                todo["done_at"] = datetime.now().isoformat()
                if close_note:
                    todo["result"] = close_note
                title_value = todo.get("title")
                closed.append(f"{item_id}（{title_value}）")
        if closed:
            save_json(TODOS_PATH, todos)
            save_json(TASKS_PATH, tasks)
            if oxy_request:
                clear_candidates(get_memory_key(oxy_request))
            if skipped:
                return f"已关闭：{', '.join(closed)}。未处理：{', '.join(skipped)}"
            return f"已关闭：{', '.join(closed)}"
        return "未找到可关闭的 id，请确认后重试。"

    if title:
        todo_matches = [idx for idx, todo in enumerate(todos) if todo.get("title") == title]
        schedule_matches = [
            idx
            for idx, task in enumerate(tasks)
            if task.get("type") == "todo_create" and (task.get("todo") or {}).get("title") == title
        ]
        if not todo_matches and not schedule_matches:
            return f"未找到标题为「{title}」的代办或排程。"
        if len(todo_matches) + len(schedule_matches) > 1:
            todo_ids = [todos[idx].get("id") for idx in todo_matches if todos[idx].get("id")]
            schedule_ids = [tasks[idx].get("id") for idx in schedule_matches if tasks[idx].get("id")]
            candidates = [*todo_ids, *schedule_ids]
            if oxy_request:
                set_candidates(get_memory_key(oxy_request), candidates)
            return f"标题重复，请提供 id。可选 id：{', '.join(candidates)}"
        if todo_matches:
            todo = todos[todo_matches[0]]
            if todo.get("status") == "done":
                return "该代办已完成，无需重复关闭。"
            todo["status"] = "done"
            todo["done_at"] = datetime.now().isoformat()
            if close_note:
                todo["result"] = close_note
            save_json(TODOS_PATH, todos)
            if oxy_request:
                clear_candidates(get_memory_key(oxy_request))
            return f"已关闭代办：{todo.get('title')}"
        task = tasks[schedule_matches[0]]
        if not task.get("enabled", True):
            return "该排程已禁用，无需重复关闭。"
        task["enabled"] = False
        task["disabled_at"] = datetime.now().isoformat()
        save_json(TASKS_PATH, tasks)
        if oxy_request:
            clear_candidates(get_memory_key(oxy_request))
        title_value = (task.get("todo") or {}).get("title") or task.get("id", "task")
        return f"已关闭排程：{title_value}"

    return "请提供要关闭的代办 id 或标题。"


@todo_fh.tool(description="查询代办任务数量与列表")
def query_todos(
    include_scheduled: bool = Field(description="是否包含重复排程", default=True),
    detail: bool = Field(description="是否返回列表详情", default=False),
    limit: int = Field(description="最多返回多少条详情", default=10),
    action: Optional[str] = Field(description="兼容参数：count/list", default=None),
) -> str:
    if action:
        action_value = action.strip().lower()
        if action_value in {"count", "统计"}:
            detail = False
        elif action_value in {"list", "detail", "详情"}:
            detail = True
    if limit <= 0:
        limit = 10
    return _build_todo_summary(include_scheduled, detail, limit)


@time_fh.tool(description="获取当前本地时间")
def get_current_time() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def _build_todo_summary(include_scheduled: bool, detail: bool, limit: int) -> str:
    todos = ensure_todos()
    open_todos = [item for item in todos if item.get("status") != "done"]

    schedules = []
    if include_scheduled:
        tasks = ensure_tasks()
        for task in tasks:
            if not task.get("enabled", True):
                continue
            if task.get("type") != "todo_create":
                continue
            schedules.append(task)

    lines = [f"当前未完成代办 {len(open_todos)} 条"]
    if include_scheduled:
        lines[0] += f"，排程 {len(schedules)} 条。"
    else:
        lines[0] += "。"

    if detail:
        if open_todos:
            lines.append("未完成代办：")
            for item in open_todos[:limit]:
                title = item.get("title", "todo")
                due_at = item.get("due_at", "-")
                action_type = item.get("action", {}).get("type", "note")
                lines.append(f"- {title} | {due_at} | {action_type}")
        if include_scheduled and schedules:
            lines.append("重复排程：")
            for task in schedules[:limit]:
                title = (task.get("todo") or {}).get("title") or task.get("id", "task")
                schedule = task.get("schedule", {})
                lines.append(f"- {title} | {format_schedule_label(schedule)}")

    return "\n".join(lines)
