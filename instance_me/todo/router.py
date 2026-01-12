import json
import logging
from typing import Any, Dict, Optional

from oxygent.schemas import OxyRequest, OxyResponse, OxyState
from oxygent.utils.common_utils import extract_first_json

from .guards import extract_ids
from .intent import llm_classify_intent

_LOGGER = logging.getLogger(__name__)
_MAX_ITERS = 10
_VALID_STATUS = {"final", "need_user", "error"}


def _parse_status_payload(output: Any) -> Optional[Dict[str, Any]]:
    if isinstance(output, dict):
        payload = output
    elif isinstance(output, str):
        try:
            payload = json.loads(extract_first_json(output))
        except Exception:
            return None
    else:
        return None
    status = str(payload.get("status") or "").strip().lower()
    if status not in _VALID_STATUS:
        return None
    return payload


def _build_followup_query(user_query: str, tool_output: str) -> str:
    return f"{user_query}\n工具结果：{tool_output}"


def _parse_decision_payload(output: Any) -> Optional[Dict[str, Any]]:
    if isinstance(output, dict):
        payload = output
    elif isinstance(output, str):
        try:
            payload = json.loads(extract_first_json(output))
        except Exception:
            return None
    else:
        return None
    action = str(payload.get("action") or "").strip().lower()
    if action not in {"return", "call"}:
        return None
    return payload


async def _decide_next_action(
    oxy_request: OxyRequest, user_query: str, payload: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    system_prompt = (
        "你是对话流程决策器，只输出 JSON。根据子 Agent 的结构化结果判断下一步动作。\n"
        "可选动作：\n"
        "- return：直接返回给用户，必须提供 message\n"
        "- call：继续调用子 Agent，必须提供 callee 与 arguments\n"
        "允许的 callee：tool_agent、todo_chat_agent。\n"
        "当 message 表示需要当前日期/当前时间/相对时间无法解析时，优先选择 call tool_agent。\n"
        "当 message 明确在向用户追问信息时，选择 return 并给出简洁中文回复。\n"
        "只输出 JSON，字段仅允许 action/callee/arguments/message。"
    )
    user_prompt = (
        f"用户问题：{user_query}\n"
        f"子 Agent 结构化结果：{json.dumps(payload, ensure_ascii=False)}\n"
        "请输出下一步 JSON："
    )
    try:
        resp = await oxy_request.call(
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
    return _parse_decision_payload(resp.output)


async def master_execute(oxy_request: OxyRequest) -> OxyResponse:
    user_query = oxy_request.get_query()
    if extract_ids(user_query):
        intent = {"intent": "todo", "action": "close"}
    else:
        intent = await llm_classify_intent(oxy_request, user_query)
    if not intent:
        return OxyResponse(
            state=OxyState.COMPLETED,
            output="我没判断出你的意图，请说明是否要新增/修改/关闭/查询代办。",
        )
    if intent.get("intent") != "todo":
        return OxyResponse(
            state=OxyState.COMPLETED,
            output="当前只支持代办管理，请说明要新增/修改/关闭/查询的代办。",
        )

    intent_action = intent.get("action")
    if intent_action == "other":
        return OxyResponse(
            state=OxyState.COMPLETED,
            output="请明确是新增/修改/关闭/查询哪种代办操作。",
        )

    current_query = user_query
    current_payload: Optional[Dict[str, Any]] = None

    for _ in range(_MAX_ITERS):
        if current_payload is None:
            response = await oxy_request.call(
                callee="todo_chat_agent",
                arguments={"query": current_query, "intent_action": intent_action},
            )
            current_payload = _parse_status_payload(response.output)
            if current_payload is None:
                output = response.output
                return OxyResponse(
                    state=OxyState.COMPLETED,
                    output=output if isinstance(output, str) else str(output),
                )

        decision = await _decide_next_action(oxy_request, user_query, current_payload)
        if not decision:
            fallback = current_payload.get("message") or "处理完成。"
            return OxyResponse(state=OxyState.COMPLETED, output=fallback)

        action = str(decision.get("action") or "").strip().lower()
        if action == "return":
            message = (decision.get("message") or "").strip()
            if not message:
                message = current_payload.get("message") or "处理完成。"
            return OxyResponse(state=OxyState.COMPLETED, output=message)

        callee = str(decision.get("callee") or "").strip()
        if not callee:
            fallback = current_payload.get("message") or "处理完成。"
            return OxyResponse(state=OxyState.COMPLETED, output=fallback)
        args = decision.get("arguments")
        if not isinstance(args, dict):
            args = {}

        if callee == "tool_agent":
            args.setdefault("query", "获取当前时间")
            tool_resp = await oxy_request.call(callee="tool_agent", arguments=args)
            tool_output = tool_resp.output
            if not isinstance(tool_output, str):
                tool_output = json.dumps(tool_output, ensure_ascii=False)
            current_query = _build_followup_query(user_query, tool_output)
            current_payload = None
            continue

        if callee == "todo_chat_agent":
            args.setdefault("query", current_query)
            args.setdefault("intent_action", intent_action)
            response = await oxy_request.call(callee="todo_chat_agent", arguments=args)
            current_payload = _parse_status_payload(response.output)
            if current_payload is None:
                output = response.output
                return OxyResponse(
                    state=OxyState.COMPLETED,
                    output=output if isinstance(output, str) else str(output),
                )
            continue

    _LOGGER.warning("todo flow exceeded max iterations", extra={"query": user_query})
    return OxyResponse(
        state=OxyState.COMPLETED,
        output="处理流程过长，请简化描述或稍后再试。",
    )
