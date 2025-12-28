# 个人 Agent 管理台设计方案

## 目标
- 提供技能库、代办任务、执行记录、Agent 状态的统一管理视图。
- 页面数据来自本地真实文件与 CodeX 技能目录，方便落地与持续更新。

## 页面范围
1. 技能库（Skill Library）
   - 展示 CodeX 技能目录下的技能列表、名称、描述、路径、系统/自定义范围。
2. 代办任务（Todo Board）
   - 展示 `todos.json` 中的任务标题、执行时间、状态与动作类型。
3. 执行记录（Execution Monitor）
   - 展示 `agent_log.txt` 中解析出的执行结果与成功/失败状态。
4. Agent 监控（Agent Monitor）
   - 展示 Agent 当前状态、最近心跳、任务列表与最近执行。

## 数据来源与文件结构
- 技能库：
  - 读取 `~/.codex/skills/**/SKILL.md` 的 YAML 头部（name/description）。
  - 系统技能识别：路径包含 `.system`。
- 代办任务：
  - `instance_me/local_file/todos.json`
- 任务调度：
  - `instance_me/local_file/agent_tasks.json`
- Agent 日志：
  - `instance_me/local_file/agent_log.txt`
- Agent 状态：
  - `instance_me/cache_dir/agent_heartbeat.json`（由 Agent 运行写入）
  - `instance_me/cache_dir/personal_agent_state.json`（任务最后运行时间）

## 本地服务与接口
本地服务：`instance_me/server.py`（端口 `8082`）

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
- `skills.html`：加载 `/api/skills`，渲染技能卡片与统计信息。
- `todos.html`：加载 `/api/todos`，渲染时间线与统计信息。
- `runs.html`：加载 `/api/runs`，渲染执行表格与摘要信息。
- `agent.html`：加载 `/api/agent`，渲染 Agent 状态与任务信息。

## 启动方式
1. 启动本地服务：
   ```bash
   python3 instance_me/server.py
   ```
2. 浏览器访问：
   - http://127.0.0.1:8082/instance_me/ui/skills.html

## 可扩展点
- Todo 创建表单直接写回 `todos.json`（新增 POST 接口）。
- Execution Monitor 解析更多日志字段（耗时、输出摘要、错误堆栈）。
- Agent Monitor 增加实时轮询与健康阈值提醒。
