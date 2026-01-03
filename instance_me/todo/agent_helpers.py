import ast
import json
import re
from typing import Any, Dict, Optional

from oxygent.schemas import LLMResponse, LLMState, OxyRequest
from oxygent.utils.common_utils import extract_first_json

from .constants import TOOL_NAMES
from .guards import guard_tool_call, map_tool_to_action
from .memory import (
    clear_pending_tool_response,
    get_memory_key,
    has_pending_tool_response,
    set_pending_tool_response,
)


def enforce_tool_reflexion(response: str, oxy_request) -> Optional[str]:
    memory_key = get_memory_key(oxy_request)
    if has_pending_tool_response(memory_key):
        return None
    intent_action = oxy_request.get_arguments("intent_action") if oxy_request else None
    if intent_action not in {"add", "update", "close", "query"}:
        return None
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
    if "arguments" not in tool_call_dict and "parameters" in tool_call_dict:
        tool_call_dict["arguments"] = tool_call_dict.pop("parameters")
    if "arguments" not in tool_call_dict or tool_call_dict["arguments"] is None:
        tool_call_dict["arguments"] = {}
    if not isinstance(tool_call_dict["arguments"], dict):
        tool_call_dict["arguments"] = {}
    return tool_call_dict


def parse_llm_response(ori_response: str, oxy_request=None) -> LLMResponse:
    memory_key = get_memory_key(oxy_request)
    try:
        if "</think>" in ori_response:
            ori_response = ori_response.split("</think>")[-1].strip()
        tool_call_dict = json.loads(extract_first_json(ori_response))
        if "tool_name" in tool_call_dict:
            tool_call_dict = normalize_tool_call(tool_call_dict)
            if oxy_request:
                intent_action = oxy_request.get_arguments("intent_action")
                tool_action = map_tool_to_action(tool_call_dict.get("tool_name", ""))
                if intent_action and tool_action and intent_action != tool_action:
                    clear_pending_tool_response(memory_key)
                    return LLMResponse(
                        state=LLMState.ANSWER,
                        output=f"我理解你的意图是{intent_action}，但系统将执行{tool_action}，请确认你要做哪一个？",
                        ori_response=ori_response,
                    )
                guarded_call, error = guard_tool_call(tool_call_dict, oxy_request)
                if error:
                    clear_pending_tool_response(memory_key)
                    return LLMResponse(
                        state=LLMState.ANSWER,
                        output=error,
                        ori_response=ori_response,
                    )
                tool_call_dict = guarded_call or tool_call_dict
            if tool_call_dict.get("tool_name"):
                set_pending_tool_response(memory_key)
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
            set_pending_tool_response(memory_key)
            return LLMResponse(
                state=LLMState.TOOL_CALL,
                output=shorthand,
                ori_response=ori_response,
            )
        if has_pending_tool_response(memory_key):
            clear_pending_tool_response(memory_key)
            return LLMResponse(
                state=LLMState.ANSWER,
                output=ori_response,
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
    except Exception as exc:
        return LLMResponse(state=LLMState.ERROR_PARSE, output=str(exc), ori_response=ori_response)


def parse_master_llm_response(ori_response: str, oxy_request=None) -> LLMResponse:
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
