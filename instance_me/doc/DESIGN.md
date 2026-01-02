# 个人 Agent 管理台设计方案

## 目标
- 提供技能库、代办任务、执行记录、Agent 状态的统一管理视图。
- 页面数据来自本地真实文件与 CodeX 技能目录，方便落地与持续更新。

## 页面范围
1. 技能库（Skill Library）
   - 仅展示 CodeX 自定义技能（系统技能不在页面中显示）。
   - 展示技能名称、描述、路径，并可查看 `SKILL.md` 详情内容。
2. 代办任务（Todo Board）
   - 展示业务待办（`todos.json` 未完成项）与业务排程（`todo_create` 任务）。
   - 不展示系统类任务（如扫描器）与已完成记录。
3. 执行记录（Execution Monitor）
   - 仅展示业务代办执行结果（日志前缀为“业务任务完成/失败”）。
   - 系统任务（如扫描器）的运行仅保留在后台日志中。
4. Agent 监控（Agent Monitor）
   - 展示 Agent 当前状态、最近心跳与任务列表。

## 数据来源与文件结构
- 技能库：
  - 读取 `~/.codex/skills/**/SKILL.md` 的 YAML 头部（name/description）。
  - 系统技能识别：路径包含 `.system`。
- 代办任务：
  - `instance_me/local_file/todos.json`（由对话 Agent 新增/修改/关闭）
- 业务排程：
  - `instance_me/local_file/agent_tasks.json` 中 `type=todo_create` 的任务
- Agent 日志：
  - `instance_me/local_file/agent_log.txt`（业务任务执行写入明确前缀）
- Agent 状态：
  - `instance_me/cache_dir/agent_heartbeat.json`（由 Agent 运行写入）
  - `instance_me/cache_dir/personal_agent_state.json`（任务最后运行时间）

## 本地服务与接口
本地服务：`instance_me/manage_service.py`（端口 `8082`）

OxyGent 服务：`instance_me/instance_me.py`（端口 `8080`，提供对话入口）
  - 可通过环境变量 `INSTANCE_ME_PORT` 或 `OXYGENT_PORT` 覆盖端口。

API 列表：
- `GET /api/skills`
  - 技能列表与统计数据
- `GET /api/todos`
  - 代办列表与统计数据
- `GET /api/runs`
  - 运行记录与统计数据
- `GET /api/agent`
  - Agent 状态、任务、最近执行

## 页面实现
- 页面：`instance_me/ui/*.html`
- 样式：`instance_me/ui/styles.css`
- 数据加载与渲染：`instance_me/ui/app.js`

页面说明：
- `skills.html`：加载 `/api/skills`，渲染自定义技能列表与详情面板。
- `todos.html`：加载 `/api/todos`，渲染业务待办与业务排程任务。
- `runs.html`：加载 `/api/runs`，渲染执行表格与摘要信息。
- `agent.html`：加载 `/api/agent`，渲染 Agent 状态与任务信息。

## Agent 编排逻辑
- MAS 入口：`instance_me/instance_me.py`
  - 启动时加载 `config.json`，以读取 `.env` 中的 LLM 配置。
- 对话 Agent：`instance_me/char_agent.py`（仅支持新增/修改/关闭业务代办）
  - 启动时会读取仓库根目录 `.env`，补充 LLM 环境变量。
  - 支持自然语言时间，由模型抽取为结构化参数（一次性或重复排程），未指定时间默认 `09:00`。
  - 提供 `query_todos` 工具查询代办数量与列表。
  - 动作类型包含：`note`、`xingyun_tag_check`、`changan_workorder_check`、`shell`。
  - 针对代办相关请求启用反射校验，要求必须通过工具调用返回结果。
  - 工具调用需输出 JSON：`{"tool_name":"...","arguments":{...}}`。
  - 兼容简写调用（如 `query_todos()`），会转换为标准工具调用。
- 路由策略：
  - `instance_me_master` 优先在代办相关请求时调用 `todo_chat_agent`，非代办问题可直接回复或引导补充信息。
- 工具 Agent：`instance_me/char_agent.py`（独立工具能力，如当前时间）
  - 对话 Agent 涉及时间时优先通过工具 Agent 获取当前时间，避免日期偏差。
  - 模型根据意图调用 `add_todo`（写入 `todos.json`）或 `add_schedule`（写入 `agent_tasks.json`）。
- 调度 Agent：`instance_me/scheduler_agent.py`（`TaskScheduler` 负责心跳、任务评估与执行编排）
- 业务执行逻辑（如 `todo_scan`、`todo_create`）由调度 Agent 内部函数执行。

## 启动方式
1. 启动本地服务：
   ```bash
   python3 instance_me/manage_service.py
   ```
2. 启动 OxyGent 服务：
   ```bash
   python3 instance_me/instance_me.py
   ```
3. 浏览器访问：
   - http://127.0.0.1:8082/instance_me/ui/skills.html
   - http://127.0.0.1:8080 （对话入口）

## 可扩展点
- 对话工具增加批量调整与附件说明。
- Execution Monitor 解析更多日志字段（耗时、输出摘要、错误堆栈）。
- Agent Monitor 增加健康阈值提醒与异常告警。
