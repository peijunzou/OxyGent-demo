import json
from typing import Dict, Optional

from oxygent.schemas import OxyRequest
from oxygent.utils.common_utils import extract_first_json


async def llm_classify_intent(
    oxy_request: Optional[OxyRequest], user_query: Optional[str]
) -> Optional[Dict[str, str]]:
    if not oxy_request or not user_query:
        return None
    system_prompt = "你是意图分类器，只输出 JSON。判断是否为代办管理请求，并给出动作类型。"
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
