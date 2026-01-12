from typing import Dict, Optional, Tuple

import logging
import os

from oxygent.schemas import OxyRequest

from .constants import ALLOWED_ACTIONS, ID_PATTERN, TOOL_NAMES, VALID_SCHEDULE_KINDS, VALID_WEEKDAYS
from config_util import get_repo_path_from_config
from .memory import (
    clear_pending_action,
    get_candidates,
    get_memory_key,
    get_pending_action,
    set_candidates,
    set_pending_action,
)
from .store import ensure_tasks, ensure_todos

_LOGGER = logging.getLogger(__name__)


def _resolve_repo_path_source(
    action_type: str, repo_path: Optional[str], env_name: str
) -> Optional[str]:
    env_value = os.getenv(env_name)
    config_value = get_repo_path_from_config(action_type)
    if repo_path:
        _LOGGER.info("%s repo_path provided: %s", action_type, repo_path)
        return repo_path
    elif env_value:
        _LOGGER.info("%s repo_path resolved from %s: %s", action_type, env_name, env_value)
        return env_value
    elif config_value:
        _LOGGER.info("%s repo_path resolved from config.json: %s", action_type, config_value)
        return config_value
    _LOGGER.warning(
        "%s repo_path missing; %s not set and config.json has no default.",
        action_type,
        env_name,
    )
    return None


def extract_ids(text: Optional[str]) -> list[str]:
    if not text:
        return []
    return ID_PATTERN.findall(text)


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
        resolved = _resolve_repo_path_source(action_type, repo_path, "XINGYUN_REPO_PATH")
        if not resolved:
            return "缺少仓库路径，请提供 repo_path。"
    if action_type == "changan_workorder_check":
        resolved = _resolve_repo_path_source(action_type, repo_path, "CHANGAN_REPO_PATH")
        if not resolved:
            return "缺少仓库路径，请提供 repo_path。"
    if action_type == "shell":
        if not command:
            return "shell 类型需要提供 command。"
    return None


def _should_use_last_candidates(query: str) -> bool:
    if not query:
        return False
    return any(token in query for token in ["上面这些", "这些ID", "这些 id", "这些编号", "这些任务", "都关闭"])


def _is_confirm_query(query: str) -> bool:
    if not query:
        return False
    return any(token in query for token in ["确认", "确定", "继续", "是的", "yes", "ok"])


def _collect_title_matches(title: str) -> Tuple[list[str], list[str]]:
    todos = ensure_todos()
    tasks = ensure_tasks()
    todo_matches = [todo.get("id") for todo in todos if todo.get("title") == title and todo.get("id")]
    schedule_matches = [
        task.get("id")
        for task in tasks
        if task.get("type") == "todo_create"
        and (task.get("todo") or {}).get("title") == title
        and task.get("id")
    ]
    return todo_matches, schedule_matches


def guard_tool_call(
    tool_call_dict: Dict[str, object], oxy_request: Optional[OxyRequest]
) -> Tuple[Optional[Dict[str, object]], Optional[str]]:
    tool_name = str(tool_call_dict.get("tool_name") or "")
    if tool_name not in TOOL_NAMES:
        return tool_call_dict, None

    arguments = tool_call_dict.get("arguments") or {}
    if not isinstance(arguments, dict):
        arguments = {}

    query = oxy_request.get_query() if oxy_request else ""
    memory_key = get_memory_key(oxy_request)

    if tool_name == "add_todo":
        title = str(arguments.get("title") or "").strip()
        due_at = str(arguments.get("due_at") or "").strip()
        action_type = str(arguments.get("action_type") or "note")
        repo_path = arguments.get("repo_path")
        command = arguments.get("command")
        if not title:
            return None, "请补充代办标题。"
        if not due_at:
            return None, "请补充执行日期和时间，例如 2026-01-08 09:00。"
        requirement_error = validate_action_requirements(action_type, repo_path, command)
        if requirement_error:
            return None, requirement_error
        return tool_call_dict, None

    if tool_name == "add_schedule":
        schedule_kind = str(arguments.get("schedule_kind") or "").strip().lower()
        action_type = str(arguments.get("action_type") or "note")
        repo_path = arguments.get("repo_path")
        command = arguments.get("command")
        if schedule_kind not in VALID_SCHEDULE_KINDS:
            return None, "请说明排程类型（daily / weekly / interval）。"
        if schedule_kind == "weekly":
            if not arguments.get("day_of_week"):
                return None, "请补充每周几，例如 周二。"
            if not arguments.get("time"):
                return None, "请补充时间，例如 11:00。"
            day = str(arguments.get("day_of_week")).lower()
            if day not in VALID_WEEKDAYS:
                return None, "day_of_week 需为 mon..sun。"
        if schedule_kind == "daily" and not arguments.get("time"):
            return None, "请补充每天的执行时间，例如 09:00。"
        if schedule_kind == "interval" and arguments.get("interval_minutes") is None:
            return None, "请补充间隔分钟数，例如 每隔 30 分钟。"
        requirement_error = validate_action_requirements(action_type, repo_path, command)
        if requirement_error:
            return None, requirement_error
        return tool_call_dict, None

    if tool_name in {"update_todo", "close_todo"}:
        todo_id = str(arguments.get("todo_id") or "").strip()
        title = str(arguments.get("title") or "").strip()
        ids = extract_ids(todo_id) or extract_ids(query)

        if tool_name == "close_todo" and not ids and _is_confirm_query(query):
            pending = get_pending_action(memory_key)
            if pending and pending.get("action") == "close":
                pending_ids = pending.get("ids") or []
                if pending_ids:
                    arguments["todo_id"] = " ".join(pending_ids)
                    tool_call_dict["arguments"] = arguments
                    clear_pending_action(memory_key)
                    return tool_call_dict, None

        if not ids and _should_use_last_candidates(query):
            candidates = get_candidates(memory_key)
            if candidates:
                arguments["todo_id"] = " ".join(candidates)
                tool_call_dict["arguments"] = arguments
                return tool_call_dict, None
            return None, "请提供具体的ID列表，以便我继续处理。"

        if ids:
            if tool_name == "close_todo" and len(ids) > 1 and not _is_confirm_query(query):
                set_pending_action(memory_key, "close", ids)
                return None, f"将关闭 {len(ids)} 个任务，请回复“确认关闭”继续。"
            return tool_call_dict, None

        if not title:
            return None, "请提供要处理的代办 id 或标题。"

        todo_ids, schedule_ids = _collect_title_matches(title)
        if not todo_ids and not schedule_ids:
            return None, f"未找到标题为「{title}」的代办或排程。"
        if tool_name == "update_todo" and schedule_ids:
            return None, "排程任务暂不支持修改，请提供代办标题或 id。"
        if len(todo_ids) + len(schedule_ids) > 1:
            candidates = [*todo_ids, *schedule_ids]
            set_candidates(memory_key, candidates)
            return None, f"标题重复，请提供 id。可选 id：{', '.join(candidates)}"

    return tool_call_dict, None
