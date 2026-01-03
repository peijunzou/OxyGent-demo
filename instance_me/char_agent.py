import ast
import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from oxygent import oxy
from oxygent.schemas import LLMResponse, LLMState, OxyRequest, OxyResponse, OxyState
from oxygent.utils.common_utils import extract_first_json
from pydantic import Field


ROOT_DIR = Path(__file__).resolve().parent
# 对话 Agent 只操作业务代办清单与重复排程。
TODOS_PATH = ROOT_DIR / "local_file" / "todos.json"
TASKS_PATH = ROOT_DIR / "local_file" / "agent_tasks.json"
ENV_PATH = ROOT_DIR.parent / ".env"

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

TODO_PROMPT = """你是 Instance Me 的代办助手，只处理新增/修改/关闭/查询代办任务。
除非需要向用户追问必要信息，否则必须使用工具调用，并严格输出 JSON（只允许 tool_name 与 arguments）：
{"tool_name":"工具名","arguments":{...}}
严禁输出 parameters / agent / action 等其他字段。
判断是否为重复任务：重复任务用 add_schedule，一次性任务用 add_todo。
查询代办或数量时使用 query_todos 工具。
动作类型支持：note / xingyun_tag_check / changan_workorder_check / shell。
时间要求：
- add_todo.due_at 必须是 YYYY-MM-DD HH:MM
- add_schedule:
  - schedule_kind: daily/weekly/interval
  - weekly: day_of_week 为 mon..sun，time 为 HH:MM
  - daily: time 为 HH:MM
  - interval: interval_minutes 为整数分钟
如用户给相对时间或“下周/明天”等，请先通过 tool_agent 获取当前时间并换算为明确日期时间。
若缺少必要信息先追问；缺少时间时必须追问，不要默认时间。
示例：
{"tool_name":"query_todos","arguments":{"detail":false}}
{"tool_name":"add_todo","arguments":{"title":"行云卡片检查","due_at":"2026-01-08 14:00","action_type":"xingyun_tag_check","repo_path":"/path"}}"""

TODO_LLM_PROMPT = """你是代办意图与要素识别器，只输出 JSON。
根据用户请求识别动作并抽取必要要素，JSON 必须包含以下字段：
- action: add/update/close/query/other
- title: 代办标题或空字符串
- todo_id: 代办 id 或空字符串
- schedule_kind: daily/weekly/interval 或空字符串（仅重复排程）
- has_date: 是否包含日期/相对日期
- has_time: 是否包含具体时间
- has_weekday: 是否包含周几
- has_interval: 是否包含间隔描述（例如每隔30分钟）
- has_update_fields: 是否明确说明要修改的内容（例如时间/标题/动作）

注意：
- 若用户表达“每天/每周/每隔”，请填写 schedule_kind。
- 若用户表达“删除/关闭/完成”，action=close。
- 若用户表达“查询/有哪些/列表/多少”，action=query。
- 只输出 JSON，不要解释。
"""

MASTER_PROMPT = """你是 Instance Me 的路由助手。
优先在代办相关请求（新增/修改/关闭/查询/排程/提醒/待办数量）时调用 todo_chat_agent。
非代办问题可直接回复或请用户补充，但不要强行调用 todo_chat_agent。
调用子代理时使用 JSON 工具调用格式，只允许 tool_name 与 arguments。
调用 todo_chat_agent 时，arguments 必须包含 query 字段。
严格禁止使用 parameters / agent / action 等字段。
示例：{"tool_name":"todo_chat_agent","arguments":{"query":"每周二上午11点，执行长安工单检查"}}"""


def enforce_tool_reflexion(response: str, oxy_request) -> Optional[str]:
    # 对涉及代办的请求强制使用工具调用，除非正在追问关键信息。
    response_text = (response or "").strip()
    if response_text:
        needs_time = any(token in response_text for token in ["时间", "日期", "几点", "哪天", "何时", "什么时候"])
        needs_schedule = any(token in response_text for token in ["每周几", "周几", "频率", "间隔", "哪天"])
        is_question = "?" in response_text or "？" in response_text or "请" in response_text
        if is_question and (needs_time or needs_schedule):
            return None
    query = oxy_request.get_query() if oxy_request else ""
    keywords = [
        "代办",
        "任务",
        "排程",
        "新增",
        "添加",
        "修改",
        "更新",
        "关闭",
        "删除",
        "查询",
        "多少",
        "列表",
        "有哪些",
        "每周",
        "每天",
        "每隔",
    ]
    if any(token in query for token in keywords):
        return "请严格输出 JSON 工具调用格式：{\"tool_name\":\"...\",\"arguments\":{...}}"
    return None


def parse_shorthand_tool_call(text: str) -> Optional[Dict[str, Any]]:
    # 兼容 query_todos() 这种简写格式，转换为标准工具调用参数。
    match = re.fullmatch(r"\s*([a-zA-Z_]\w*)\((.*)\)\s*", text.strip())
    if not match:
        return None
    name, args_src = match.groups()
    if name not in TOOL_NAMES:
        return None
    if not args_src.strip():
        return {"tool_name": name, "arguments": {}}
    try:
        call = ast.parse(f"f({args_src})", mode="eval").body
    except SyntaxError:
        return None
    if not isinstance(call, ast.Call):
        return None
    arguments: Dict[str, Any] = {}
    for keyword in call.keywords:
        if keyword.arg is None:
            return None
        try:
            arguments[keyword.arg] = ast.literal_eval(keyword.value)
        except ValueError:
            return None
    return {"tool_name": name, "arguments": arguments}


def normalize_tool_call(tool_call_dict: Dict[str, Any]) -> Dict[str, Any]:
    # 兼容 parameters 字段，统一为 arguments。
    if "arguments" not in tool_call_dict and "parameters" in tool_call_dict:
        tool_call_dict["arguments"] = tool_call_dict.pop("parameters")
    if "arguments" not in tool_call_dict or tool_call_dict["arguments"] is None:
        tool_call_dict["arguments"] = {}
    if not isinstance(tool_call_dict["arguments"], dict):
        tool_call_dict["arguments"] = {}
    return tool_call_dict


def parse_llm_response(ori_response: str, oxy_request=None) -> LLMResponse:
    # 自定义解析，优先识别 JSON 工具调用，其次兼容简写调用。
    try:
        if "</think>" in ori_response:
            ori_response = ori_response.split("</think>")[-1].strip()
        tool_call_dict = json.loads(extract_first_json(ori_response))
        if "tool_name" in tool_call_dict:
            tool_call_dict = normalize_tool_call(tool_call_dict)
            if oxy_request:
                arguments = tool_call_dict.get("arguments") or {}
                intent_action = oxy_request.get_arguments("intent_action")
                tool_action = map_tool_to_action(tool_call_dict.get("tool_name", ""))
                if intent_action and tool_action and intent_action != tool_action:
                    return LLMResponse(
                        state=LLMState.ANSWER,
                        output=f"我理解你的意图是{intent_action}，但系统将执行{tool_action}，请确认你要做哪一个？",
                        ori_response=ori_response,
                    )
                if tool_call_dict.get("tool_name") in {"add_todo", "add_schedule"}:
                    raw_query = oxy_request.get_arguments("raw_query") or oxy_request.get_query()
                    arguments.setdefault("user_query", raw_query)
                    tool_call_dict["arguments"] = arguments
            return LLMResponse(
                state=LLMState.TOOL_CALL,
                output=tool_call_dict,
                ori_response=ori_response,
            )
        return LLMResponse(
            state=LLMState.ERROR_PARSE,
            output="请严格输出 JSON 工具调用格式。",
            ori_response=ori_response,
        )
    except json.JSONDecodeError:
        shorthand = parse_shorthand_tool_call(ori_response)
        if shorthand:
            return LLMResponse(
                state=LLMState.TOOL_CALL,
                output=shorthand,
                ori_response=ori_response,
            )
        reflection_msg = enforce_tool_reflexion(ori_response, oxy_request)
        if reflection_msg:
            return LLMResponse(
                state=LLMState.ERROR_PARSE,
                output=reflection_msg,
                ori_response=ori_response,
            )
        return LLMResponse(
            state=LLMState.ANSWER,
            output=ori_response,
            ori_response=ori_response,
        )


def parse_master_llm_response(ori_response: str, oxy_request=None) -> LLMResponse:
    # 主代理允许直接回复，同时修正常见的工具调用字段。
    try:
        if "</think>" in ori_response:
            ori_response = ori_response.split("</think>")[-1].strip()
        tool_call_dict = json.loads(extract_first_json(ori_response))
        if "tool_name" in tool_call_dict:
            tool_call_dict = normalize_tool_call(tool_call_dict)
            return LLMResponse(
                state=LLMState.TOOL_CALL,
                output=tool_call_dict,
                ori_response=ori_response,
            )
        return LLMResponse(state=LLMState.ANSWER, output=ori_response, ori_response=ori_response)
    except json.JSONDecodeError:
        return LLMResponse(state=LLMState.ANSWER, output=ori_response, ori_response=ori_response)
    except Exception as exc:
        return LLMResponse(state=LLMState.ERROR_PARSE, output=str(exc), ori_response=ori_response)


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


def normalize_time(value: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    # 校验 HH:MM 时间格式，返回标准化后的字符串。
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
    # 将时间归一化为 YYYY-MM-DD HH:MM，并拦截早于当前时间的输入。
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


async def llm_check_time_flags(
    oxy_request: Optional[OxyRequest], user_query: Optional[str]
) -> Dict[str, bool]:
    if not oxy_request or not user_query:
        return {}
    system_prompt = (
        "你是时间信息校验器，只输出 JSON。判断用户请求是否包含明确的日期、时间、星期几或间隔描述。"
    )
    user_prompt = (
        "返回 JSON，键仅包含 has_date, has_time, has_weekday, has_interval。\n"
        "说明：\n"
        "- has_date: 包含具体日期或相对日期（如 2026-01-08、下周二、明天）\n"
        "- has_time: 包含具体时间（如 09:00、上午11点、今晚8点）\n"
        "- has_weekday: 明确包含周几（如 周二/星期二/礼拜二）\n"
        "- has_interval: 明确包含间隔（如 每隔30分钟/每隔2小时）\n"
        f"用户请求：{user_query}"
    )
    try:
        response = await oxy_request.call(
            callee="default_llm",
            arguments={
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0,
            },
        )
    except Exception:
        return {}
    try:
        parsed = json.loads(extract_first_json(response.output or ""))
    except Exception:
        return {}
    return {
        "has_date": bool(parsed.get("has_date")),
        "has_time": bool(parsed.get("has_time")),
        "has_weekday": bool(parsed.get("has_weekday")),
        "has_interval": bool(parsed.get("has_interval")),
    }


async def llm_classify_intent(
    oxy_request: Optional[OxyRequest], user_query: Optional[str]
) -> Optional[Dict[str, str]]:
    if not oxy_request or not user_query:
        return None
    system_prompt = (
        "你是意图分类器，只输出 JSON。判断是否为代办管理请求，并给出动作类型。"
    )
    user_prompt = (
        "返回 JSON，键仅包含 intent 与 action。\n"
        "- intent: todo 或 other\n"
        "- action: add/update/close/query/other\n"
        f"用户请求：{user_query}"
    )
    try:
        response = await oxy_request.call(
            callee="default_llm",
            arguments={
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0,
            },
        )
    except Exception:
        return None
    try:
        parsed = json.loads(extract_first_json(response.output or ""))
    except Exception:
        return None
    intent = str(parsed.get("intent", "")).strip().lower()
    action = str(parsed.get("action", "")).strip().lower()
    if intent not in {"todo", "other"}:
        intent = "other"
    if action not in {"add", "update", "close", "query", "other"}:
        action = "other"
    return {"intent": intent, "action": action}


async def llm_todo_gate(
    oxy_request: Optional[OxyRequest], user_query: Optional[str]
) -> Optional[Dict[str, Any]]:
    if not oxy_request or not user_query:
        return None
    try:
        response = await oxy_request.call(
            callee="todo_llm_agent",
            arguments={"query": user_query},
        )
    except Exception:
        return None
    try:
        parsed = json.loads(extract_first_json(response.output or ""))
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def normalize_gate_value(value: Optional[str]) -> str:
    return str(value or "").strip()


def validate_todo_gate(
    gate: Dict[str, Any], todos: List[Dict[str, Any]]
) -> Optional[str]:
    action = normalize_gate_value(gate.get("action")).lower()
    title = normalize_gate_value(gate.get("title"))
    todo_id = normalize_gate_value(gate.get("todo_id"))
    schedule_kind = normalize_gate_value(gate.get("schedule_kind")).lower()
    has_date = bool(gate.get("has_date"))
    has_time = bool(gate.get("has_time"))
    has_weekday = bool(gate.get("has_weekday"))
    has_interval = bool(gate.get("has_interval"))
    has_update_fields = bool(gate.get("has_update_fields"))

    if action not in {"add", "update", "close", "query"}:
        return "请说明你要新增、修改、关闭还是查询代办。"

    if action == "add":
        if not schedule_kind:
            if has_interval:
                schedule_kind = "interval"
            elif has_weekday:
                schedule_kind = "weekly"
        if schedule_kind in VALID_SCHEDULE_KINDS:
            if schedule_kind == "weekly":
                missing = []
                if not has_weekday:
                    missing.append("每周几")
                if not has_time:
                    missing.append("时间")
                if missing:
                    return f"请补充{ '、'.join(missing) }，例如 每周二 11:00。"
            elif schedule_kind == "daily":
                if not has_time:
                    return "请补充每天的执行时间，例如 每天 09:00。"
            elif schedule_kind == "interval":
                if not has_interval:
                    return "请补充间隔分钟数，例如 每隔 30 分钟。"
        else:
            if not has_date and not has_time:
                return "请补充执行日期和时间，例如 2026-01-08 09:00 或 下周二上午11点。"
            if not has_date:
                return "请补充执行日期，例如 2026-01-08 或 下周二。"
            if not has_time:
                return "请补充执行时间，例如 09:00 或 上午11点。"
        if not title:
            return "请补充代办标题。"

    if action == "update":
        if not (todo_id or title):
            return "请提供要修改的代办 id 或标题。"
        if title:
            matches = [todo for todo in todos if todo.get("title") == title]
            if not matches:
                return f"未找到标题为「{title}」的代办。"
            if len(matches) > 1:
                ids = [todo.get("id") for todo in matches if todo.get("id")]
                return f"标题重复，请提供 id。可选 id：{', '.join(ids)}"
        if not has_update_fields:
            return "请说明要修改哪些内容，例如改时间或改标题。"

    if action == "close":
        if not (todo_id or title):
            return "请提供要关闭的代办 id 或标题。"
        if title:
            matches = [todo for todo in todos if todo.get("title") == title]
            if not matches:
                return f"未找到标题为「{title}」的代办。"
            if len(matches) > 1:
                ids = [todo.get("id") for todo in matches if todo.get("id")]
                return f"标题重复，请提供 id。可选 id：{', '.join(ids)}"

    return None


def map_tool_to_action(tool_name: str) -> Optional[str]:
    if tool_name in {"add_todo", "add_schedule"}:
        return "add"
    if tool_name == "update_todo":
        return "update"
    if tool_name == "close_todo":
        return "close"
    if tool_name == "query_todos":
        return "query"
    return None


def validate_action_requirements(
    action_type: str,
    repo_path: Optional[str],
    command: Optional[str],
) -> Optional[str]:
    if action_type == "xingyun_tag_check":
        if not (repo_path or os.getenv("XINGYUN_REPO_PATH")):
            return "缺少仓库路径，请提供 repo_path 或设置 XINGYUN_REPO_PATH。"
    if action_type == "changan_workorder_check":
        if not (repo_path or os.getenv("CHANGAN_REPO_PATH")):
            return "缺少仓库路径，请提供 repo_path 或设置 CHANGAN_REPO_PATH。"
    if action_type == "shell":
        if not command:
            return "shell 类型需要提供 command。"
    return None


async def master_execute(oxy_request: OxyRequest) -> OxyResponse:
    user_query = oxy_request.get_query()
    intent = await llm_classify_intent(oxy_request, user_query)
    if not intent:
        return OxyResponse(
            state=OxyState.COMPLETED,
            output="我没判断出你的意图，请说明是否要新增/修改/关闭/查询代办。",
        )
    if intent["intent"] == "todo":
        gate = await llm_todo_gate(oxy_request, user_query)
        if not gate:
            return OxyResponse(
                state=OxyState.COMPLETED,
                output="我没判断出你的代办意图，请再描述一次。",
            )
        todos = ensure_todos()
        gate_msg = validate_todo_gate(gate, todos)
        if gate_msg:
            return OxyResponse(state=OxyState.COMPLETED, output=gate_msg)
        action = normalize_gate_value(gate.get("action")).lower()
        schedule_kind = normalize_gate_value(gate.get("schedule_kind")).lower()
        return await oxy_request.call(
            callee="todo_chat_agent",
            arguments={
                "query": user_query,
                "raw_query": user_query,
                "intent_action": action,
                "schedule_kind": schedule_kind,
            },
        )
    return OxyResponse(
        state=OxyState.COMPLETED,
        output="当前只支持代办管理，请说明要新增/修改/关闭/查询的代办。",
    )


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
    # 生成排程的人类可读描述。
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
    # 将重复排程写入 agent_tasks.json，由调度器自动生成代办。
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
    # 读取重复排程清单。
    data = load_json(TASKS_PATH, [])
    return data if isinstance(data, list) else []


def build_todo_summary(
    include_scheduled: bool, detail: bool, limit: int
) -> str:
    # 汇总代办与排程信息，供对话查询使用。
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
                title = task.get("todo", {}).get("title") or task.get("id", "task")
                schedule = task.get("schedule", {})
                lines.append(f"- {title} | {format_schedule_label(schedule)}")

    return "\n".join(lines)


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


@todo_fh.tool(description="新增一次性代办任务，时间需为明确日期")
async def add_todo(
    title: str = Field(description="代办标题"),
    due_at: str = Field(description="执行时间，格式 YYYY-MM-DD HH:MM"),
    action_type: str = Field(description="动作类型：note / xingyun_tag_check / changan_workorder_check / shell", default="note"),
    action_message: Optional[str] = Field(description="note 类型的说明", default=None),
    repo_path: Optional[str] = Field(description="xingyun_tag_check 的仓库路径", default=None),
    test_mode: Optional[bool] = Field(description="xingyun_tag_check 是否测试模式", default=None),
    command: Optional[str] = Field(description="shell 命令", default=None),
    workdir: Optional[str] = Field(description="shell 工作目录", default=None),
    args: Optional[str] = Field(description="shell 参数，逗号分隔", default=None),
    user_query: Optional[str] = Field(description="内部字段：用户原始请求", default=None),
    oxy_request: OxyRequest = None,
) -> str:
    if action_type not in ALLOWED_ACTIONS:
        return f"不支持的 action_type：{action_type}"
    requirement_error = validate_action_requirements(action_type, repo_path, command)
    if requirement_error:
        return requirement_error
    flags = await llm_check_time_flags(oxy_request, user_query)
    if user_query:
        if not flags:
            return "时间信息识别失败，请补充执行日期和时间。"
        if not flags.get("has_date") and not flags.get("has_time"):
            return "请补充执行日期和时间，例如 2026-01-08 09:00 或 下周二上午11点。"
        if not flags.get("has_date"):
            return "请补充执行日期，例如 2026-01-08 或 下周二。"
        if not flags.get("has_time"):
            return "请补充执行时间，例如 09:00 或 上午11点。"
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
async def add_schedule(
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
    user_query: Optional[str] = Field(description="内部字段：用户原始请求", default=None),
    oxy_request: OxyRequest = None,
) -> str:
    if action_type not in ALLOWED_ACTIONS:
        return f"不支持的 action_type：{action_type}"
    if schedule_kind not in VALID_SCHEDULE_KINDS:
        return f"不支持的 schedule_kind：{schedule_kind}"

    requirement_error = validate_action_requirements(action_type, repo_path, command)
    if requirement_error:
        return requirement_error
    flags = await llm_check_time_flags(oxy_request, user_query)
    if user_query:
        if not flags:
            return "时间信息识别失败，请补充排程时间信息。"
        if schedule_kind == "weekly":
            missing = []
            if not flags.get("has_weekday"):
                missing.append("每周几")
            if not flags.get("has_time"):
                missing.append("时间")
            if missing:
                return f"请补充{ '、'.join(missing) }，例如 每周二 11:00。"
        elif schedule_kind == "daily":
            if not flags.get("has_time"):
                return "请补充每天的执行时间，例如 每天 09:00。"
        elif schedule_kind == "interval":
            if not flags.get("has_interval"):
                return "请补充间隔分钟数，例如 每隔 30 分钟。"

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
    return build_todo_summary(include_scheduled, detail, limit)


@time_fh.tool(description="获取当前本地时间")
def get_current_time() -> str:
    # 供工具 Agent 提供当前时间，避免生成过期日期。
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
            name="tool_agent",
            desc="提供时间等基础工具能力的辅助 Agent",
            llm_model="default_llm",
            tools=["time_tools"],
        ),
        oxy.ReActAgent(
            name="todo_llm_agent",
            desc="代办意图与要素识别辅助 Agent",
            prompt=TODO_LLM_PROMPT,
            llm_model="default_llm",
            func_parse_llm_response=parse_master_llm_response,
        ),
        oxy.ReActAgent(
            name="todo_chat_agent",
            desc="只负责新增、修改、关闭业务代办任务的对话助手",
            prompt=TODO_PROMPT,
            llm_model="default_llm",
            tools=["todo_tools"],
            func_parse_llm_response=parse_llm_response,
            func_reflexion=enforce_tool_reflexion,
        ),
        oxy.ReActAgent(
            name="instance_me_master",
            is_master=True,
            prompt=MASTER_PROMPT,
            llm_model="default_llm",
            func_parse_llm_response=parse_master_llm_response,
            func_execute=master_execute,
            sub_agents=["todo_chat_agent", "todo_llm_agent", "tool_agent"],
        ),
    ]
