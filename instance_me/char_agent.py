import os
import sys
from pathlib import Path

from oxygent import oxy

ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from todo.actions import time_fh, todo_fh
from todo.agent_helpers import (
    enforce_tool_reflexion,
    parse_llm_response,
    parse_master_llm_response,
)
from todo.prompts import MASTER_PROMPT, TODO_LLM_PROMPT, TODO_PROMPT, apply_user_style
from todo.router import master_execute
from todo.store import ENV_PATH, load_env_file


def build_oxy_space():
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
            prompt=apply_user_style(TODO_PROMPT),
            llm_model="default_llm",
            tools=["todo_tools"],
            sub_agents=["todo_llm_agent", "tool_agent"],
            func_parse_llm_response=parse_llm_response,
            func_reflexion=enforce_tool_reflexion,
        ),
        oxy.ReActAgent(
            name="instance_me_master",
            is_master=True,
            prompt=apply_user_style(MASTER_PROMPT),
            llm_model="default_llm",
            func_parse_llm_response=parse_master_llm_response,
            func_execute=master_execute,
            sub_agents=["todo_chat_agent", "todo_llm_agent", "tool_agent"],
        ),
    ]
