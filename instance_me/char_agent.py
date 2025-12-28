import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from oxygent import oxy
from pydantic import Field


ROOT_DIR = Path(__file__).resolve().parent
# 对话 Agent 只操作业务代办清单。
TODOS_PATH = ROOT_DIR / "local_file" / "todos.json"
TASKS_PATH = ROOT_DIR / "local_file" / "agent_tasks.json"
ENV_PATH = ROOT_DIR.parent / ".env"

ALLOWED_ACTIONS = {"note", "xingyun_tag_check", "shell"}
DEFAULT_DUE_TIME = "09:00"
WEEKDAY_CN = {
    "一": 0,
    "二": 1,
    "三": 2,
    "四": 3,
    "五": 4,
    "六": 5,
    "日": 6,
    "天": 6,
}
WEEKDAY_EN = {
    "一": "mon",
    "二": "tue",
    "三": "wed",
    "四": "thu",
    "五": "fri",
    "六": "sat",
    "日": "sun",
    "天": "sun",
}


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_env_file(path: Path) -> None:
    # 兼容本地 .env 配置，避免环境变量缺失导致启动失败。
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def validate_due_at(value: str) -> Optional[str]:
    # 统一验证代办执行时间格式：YYYY-MM-DD HH:MM 或 YYYY-MM-DD HH:MM:SS。
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            datetime.strptime(value, fmt)
            return value
        except ValueError:
            continue
    return None


def parse_time_from_text(text: str) -> Optional[Tuple[int, int]]:
    # 解析 HH:MM 或 “下午2点半” 类表达。
    m = re.search(r"(\d{1,2})[:：](\d{2})", text)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour, minute
        return None
    m = re.search(r"(上午|下午|晚上|中午)?\s*(\d{1,2})[点时](\d{1,2})?分?", text)
    if not m:
        return None
    period = m.group(1) or ""
    hour = int(m.group(2))
    minute = int(m.group(3) or 0)
    if period in {"下午", "晚上"} and hour < 12:
        hour += 12
    if period == "中午" and 0 < hour < 11:
        hour += 12
    if period == "上午" and hour == 12:
        hour = 0
    if 0 <= hour <= 23 and 0 <= minute <= 59:
        return hour, minute
    return None


def parse_natural_due_at(value: str, now: datetime) -> Optional[str]:
    raw = value.strip()
    if not raw:
        return None

    time_tuple = parse_time_from_text(raw)
    if time_tuple:
        hour, minute = time_tuple
    else:
        hour, minute = map(int, DEFAULT_DUE_TIME.split(":"))

    if "今天" in raw:
        target_date = now.date()
    elif "明天" in raw:
        target_date = (now + timedelta(days=1)).date()
    elif "后天" in raw:
        target_date = (now + timedelta(days=2)).date()
    else:
        m = re.search(r"(下周|下星期|下礼拜)([一二三四五六日天])", raw)
        if m:
            target = WEEKDAY_CN.get(m.group(2))
            if target is None:
                return None
            days_until_next_week = 7 - now.weekday()
            target_date = (now + timedelta(days=days_until_next_week + target)).date()
        else:
            m = re.search(r"(周|星期|礼拜)([一二三四五六日天])", raw)
            if not m:
                return None
            target = WEEKDAY_CN.get(m.group(2))
            if target is None:
                return None
            days_ahead = (target - now.weekday()) % 7
            candidate = now + timedelta(days=days_ahead)
            target_date = candidate.date()

    due_dt = datetime.combine(target_date, datetime.min.time()).replace(
        hour=hour, minute=minute
    )
    return due_dt.strftime("%Y-%m-%d %H:%M")


def normalize_due_at(value: str) -> Tuple[Optional[str], Optional[str]]:
    if not value:
        return None, "缺少执行时间。"

    now = datetime.now()
    parsed = validate_due_at(value)
    if parsed:
        parsed_dt = datetime.strptime(parsed[:16], "%Y-%m-%d %H:%M")
        if parsed_dt < now - timedelta(minutes=1):
            return None, "执行时间早于当前时间，请确认。"
        return parsed[:16], None

    m = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", value)
    if m:
        date_only = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        parsed_value = f"{date_only} {DEFAULT_DUE_TIME}"
        parsed_dt = datetime.strptime(parsed_value, "%Y-%m-%d %H:%M")
        if parsed_dt < now - timedelta(minutes=1):
            return None, "执行时间早于当前时间，请确认。"
        return parsed_value, None

    natural = parse_natural_due_at(value, now)
    if natural:
        natural_dt = datetime.strptime(natural, "%Y-%m-%d %H:%M")
        if natural_dt < now - timedelta(minutes=1):
            return None, "执行时间早于当前时间，请确认。"
        return natural, None

    return None, "执行时间格式错误，请使用 YYYY-MM-DD HH:MM 或自然语言（如：下周一 14:00）。"


def build_action(
    action_type: str,
    message: Optional[str],
    repo_path: Optional[str],
    test_mode: Optional[bool],
    command: Optional[str],
    workdir: Optional[str],
    args: Optional[str],
) -> Dict[str, Any]:
    # 根据动作类型拼装 action，保持与调度器一致的结构。
    action: Dict[str, Any] = {"type": action_type}
    if action_type == "note":
        action["message"] = message or "待办事项"
    elif action_type == "xingyun_tag_check":
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


def parse_schedule_text(text: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    raw = text.strip()
    if not raw:
        return None, "缺少执行时间。"

    interval_match = re.search(r"每隔\s*(\d+)\s*(分钟|小时)", raw)
    if not interval_match:
        interval_match = re.search(r"每\s*(\d+)\s*(分钟|小时)", raw)
    if interval_match:
        amount = int(interval_match.group(1))
        unit = interval_match.group(2)
        minutes = amount * 60 if unit == "小时" else amount
        if minutes <= 0:
            return None, "间隔时间需要大于 0。"
        return {"kind": "interval", "minutes": minutes}, None

    if "每天" in raw or "每日" in raw:
        time_tuple = parse_time_from_text(raw)
        if time_tuple:
            hour, minute = time_tuple
            time_str = f"{hour:02d}:{minute:02d}"
        else:
            time_str = DEFAULT_DUE_TIME
        return {"kind": "daily", "time": time_str}, None

    if "每周" in raw or "每星期" in raw or "每礼拜" in raw:
        match = re.search(r"(每周|每星期|每礼拜)\s*([一二三四五六日天])", raw)
        if not match:
            return None, "重复任务需要指定星期几（如：每周四 14:00）。"
        day_cn = match.group(2)
        day_en = WEEKDAY_EN.get(day_cn)
        if not day_en:
            return None, "无法识别星期信息。"
        time_tuple = parse_time_from_text(raw)
        if time_tuple:
            hour, minute = time_tuple
            time_str = f"{hour:02d}:{minute:02d}"
        else:
            time_str = DEFAULT_DUE_TIME
        return {"kind": "weekly", "day_of_week": day_en, "time": time_str}, None

    return None, None


def create_schedule_task(title: str, schedule: Dict[str, Any], action: Dict[str, Any]) -> str:
    tasks = ensure_tasks()
    task_id = f"schedule-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    task_item = {
        "id": task_id,
        "type": "todo_create",
        "schedule": schedule,
        "enabled": True,
        "todo": {
            "title": title,
            "action": action,
        },
    }
    tasks.append(task_item)
    save_json(TASKS_PATH, tasks)
    return f"已新增重复任务：{title}，{format_schedule_label(schedule)}"


def ensure_todos() -> List[Dict[str, Any]]:
    data = load_json(TODOS_PATH, [])
    return data if isinstance(data, list) else []


def ensure_tasks() -> List[Dict[str, Any]]:
    data = load_json(TASKS_PATH, [])
    return data if isinstance(data, list) else []


def find_todo(
    todos: List[Dict[str, Any]], todo_id: Optional[str], title: Optional[str]
) -> Tuple[Optional[int], Optional[str]]:
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
            ids = [todos[idx].get("id") for idx in matches]
            return None, f"标题重复，请提供 id。可选 id：{', '.join(ids)}"
        return matches[0], None

    return None, "需要提供 todo_id 或 title 用于定位代办。"


todo_fh = oxy.FunctionHub(name="todo_tools")
time_fh = oxy.FunctionHub(name="time_tools")


@todo_fh.tool(description="新增代办任务，若时间包含“每周/每天/每隔”会自动创建重复排程")
def add_todo(
    title: str = Field(description="代办标题"),
    due_at: str = Field(description="执行时间，支持 YYYY-MM-DD HH:MM 或自然语言（如下周一 14:00）"),
    action_type: str = Field(description="动作类型：note / xingyun_tag_check / shell", default="note"),
    action_message: Optional[str] = Field(description="note 类型的说明", default=None),
    repo_path: Optional[str] = Field(description="xingyun_tag_check 的仓库路径", default=None),
    test_mode: Optional[bool] = Field(description="xingyun_tag_check 是否测试模式", default=None),
    command: Optional[str] = Field(description="shell 命令", default=None),
    workdir: Optional[str] = Field(description="shell 工作目录", default=None),
    args: Optional[str] = Field(description="shell 参数，逗号分隔", default=None),
) -> str:
    if action_type not in ALLOWED_ACTIONS:
        return f"不支持的 action_type：{action_type}"
    action = build_action(action_type, action_message or title, repo_path, test_mode, command, workdir, args)
    schedule, schedule_err = parse_schedule_text(due_at)
    if schedule_err:
        return schedule_err
    if schedule:
        return create_schedule_task(title, schedule, action)

    due_value, err = normalize_due_at(due_at)
    if err:
        return err

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


@todo_fh.tool(description="修改业务代办任务的标题或时间")
def update_todo(
    todo_id: Optional[str] = Field(description="代办 id", default=None),
    title: Optional[str] = Field(description="原标题，用于查找代办", default=None),
    new_title: Optional[str] = Field(description="新的标题", default=None),
    new_due_at: Optional[str] = Field(description="新的执行时间，支持 YYYY-MM-DD HH:MM 或自然语言", default=None),
    new_action_type: Optional[str] = Field(description="新的动作类型", default=None),
    new_action_message: Optional[str] = Field(description="新的动作说明", default=None),
    repo_path: Optional[str] = Field(description="更新 xingyun_tag_check 的仓库路径", default=None),
    test_mode: Optional[bool] = Field(description="更新 xingyun_tag_check 的测试模式", default=None),
    command: Optional[str] = Field(description="更新 shell 命令", default=None),
    workdir: Optional[str] = Field(description="更新 shell 工作目录", default=None),
    args: Optional[str] = Field(description="更新 shell 参数，逗号分隔", default=None),
) -> str:
    todos = ensure_todos()
    idx, err = find_todo(todos, todo_id, title)
    if err:
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
    return f"已更新代办：{todo.get('title')}（{', '.join(changes)}）"


@todo_fh.tool(description="关闭业务代办任务")
def close_todo(
    todo_id: Optional[str] = Field(description="代办 id", default=None),
    title: Optional[str] = Field(description="原标题，用于查找代办", default=None),
    close_note: Optional[str] = Field(description="关闭说明", default=None),
) -> str:
    todos = ensure_todos()
    idx, err = find_todo(todos, todo_id, title)
    if err:
        return err

    todo = todos[idx]
    if todo.get("status") == "done":
        return "该代办已完成，无需重复关闭。"

    todo["status"] = "done"
    todo["done_at"] = datetime.now().isoformat()
    if close_note:
        todo["result"] = close_note
    save_json(TODOS_PATH, todos)
    return f"已关闭代办：{todo.get('title')}"


@time_fh.tool(description="获取当前本地时间")
def get_current_time() -> str:
    # 供对话 Agent 获取当前时间，避免生成过期日期。
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def build_oxy_space():
    # 只暴露代办管理能力，避免对话 Agent 处理系统级任务。
    load_env_file(ENV_PATH)
    return [
        oxy.HttpLLM(
            name="default_llm",
            api_key=os.getenv("DEFAULT_LLM_API_KEY"),
            base_url=os.getenv("DEFAULT_LLM_BASE_URL"),
            model_name=os.getenv("DEFAULT_LLM_MODEL_NAME"),
        ),
        todo_fh,
        time_fh,
        oxy.ReActAgent(
            name="todo_chat_agent",
            desc="只负责新增、修改、关闭业务代办任务的对话助手",
            llm_model="default_llm",
            tools=["todo_tools"],
        ),
        oxy.ReActAgent(
            name="tool_agent",
            desc="提供时间等基础工具能力的辅助 Agent",
            llm_model="default_llm",
            tools=["time_tools"],
        ),
        oxy.ReActAgent(
            name="instance_me_master",
            is_master=True,
            llm_model="default_llm",
            sub_agents=["todo_chat_agent", "tool_agent"],
        ),
    ]
