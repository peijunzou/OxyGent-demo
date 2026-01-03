# Instance Me Agent 架构（弱编排 + 强 ReAct）

## 目标
- `instance_me_master` 只负责路由，不做 CRUD 决策。
- `todo_chat_agent` 负责对话流与工具调用（强 ReAct）。
- 确定性 Guard 拦截不完整或危险的工具调用。
- 短期记忆本地可用，无需 ES 支撑当前页面会话。

## Agent 角色
- `instance_me_master`
  - 识别领域（todo vs other）。
  - 代办相关请求路由到 `todo_chat_agent`。
- `todo_chat_agent`
  - 强 ReAct：对话、抽取、工具调用。
  - 可调用 `todo_llm_agent` 做意图/槽位识别提示。
- `todo_llm_agent`
  - 仅输出结构化 JSON（action + slots）。
  - 不执行工具。
- `tool_agent`
  - 工具辅助能力（如获取当前时间）。

## 关键流程
1) 用户请求 → `instance_me_master`（意图路由）
2) `todo_chat_agent`（ReAct）→ 可选调用 `todo_llm_agent`
3) 工具调用 Guard 校验
4) 执行工具（CRUD）
5) 记录短期记忆候选项，支持后续对话引用

## 工具调用 Guard（前置校验）
- 意图一致性：`intent_action` 与工具动作不一致时拦截并追问。
- 必填字段：
  - `add_todo`：`title` + `due_at`
  - `add_schedule`：`schedule_kind` +（weekly: `day_of_week` + `time`，daily: `time`，interval: `interval_minutes`）
  - `update_todo`：`todo_id`/`title` + 至少一个更新字段
  - `close_todo`：`todo_id`/`title` 且需唯一定位
- 依赖检查：
  - `xingyun_tag_check` / `changan_workorder_check` 需要 `repo_path` 或环境变量
  - `shell` 需要 `command`
- 批量关闭：多 ID 必须二次确认。
- 给出 ID 时优先用 ID，不再被同名标题拦截。

## 短期记忆（无 ES）
- 内存缓存键：`group_id` / `from_trace_id` / `trace_id`。
- `last_candidates`：标题重复时保存候选 ID 列表。
- 支持“上面这些 ID”通过 `last_candidates` 复用。
- `pending_action`：批量关闭需确认时暂存。
- TTL：30 分钟（自动过期）。

## 文件结构
- `instance_me/char_agent.py`：Agent 编排入口
- `instance_me/todo/prompts.py`：提示词 + 用户风格注入
- `instance_me/todo/intent.py`：意图路由分类
- `instance_me/todo/agent_helpers.py`：工具解析与 Guard 接入
- `instance_me/todo/guards.py`：确定性校验
- `instance_me/todo/memory.py`：短期记忆
- `instance_me/todo/store.py`：本地 JSON 持久化
- `instance_me/todo/actions.py`：CRUD 工具实现
