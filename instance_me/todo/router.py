from typing import Optional

from oxygent.schemas import OxyRequest, OxyResponse, OxyState

from .guards import extract_ids
from .intent import llm_classify_intent


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
    if intent["intent"] == "todo":
        return await oxy_request.call(
            callee="todo_chat_agent",
            arguments={"query": user_query, "intent_action": intent.get("action")},
        )
    return OxyResponse(
        state=OxyState.COMPLETED,
        output="当前只支持代办管理，请说明要新增/修改/关闭/查询的代办。",
    )
