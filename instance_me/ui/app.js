const reveals = document.querySelectorAll(".reveal");

const setupReveals = () => {
  reveals.forEach((el) => {
    const delay = el.getAttribute("data-delay");
    if (delay) {
      el.style.setProperty("--delay", `${delay}ms`);
    }
  });
  requestAnimationFrame(() => {
    document.body.classList.add("is-ready");
  });
};

const fetchJSON = async (path) => {
  try {
    const res = await fetch(path);
    if (!res.ok) {
      throw new Error(`Request failed: ${res.status}`);
    }
    return await res.json();
  } catch (err) {
    console.warn("Failed to load", path, err);
    return null;
  }
};

const setText = (selector, value) => {
  const el = document.querySelector(selector);
  if (el) {
    el.textContent = value;
  }
};

const formatDate = (value) => {
  if (!value) return "-";
  return value.replace("T", " ").slice(0, 16);
};

const LABEL_MAP = {
  system: "系统",
  custom: "自定义",
  open: "进行中",
  done: "已完成",
  scheduled: "已排程",
  failed: "失败",
  success: "成功",
  todo_scan: "代办扫描",
  todo_create: "创建代办",
  xingyun_tag_check: "行云卡片检查",
  changan_workorder_check: "长安工单检查",
  note: "备注",
  shell: "脚本执行",
  active: "运行中",
  idle: "空闲",
  unknown: "未知",
};

const STATUS_NOTE_MAP = {
  "heartbeat ok": "心跳正常",
  "no recent heartbeat": "心跳超时",
  "no heartbeat file": "未发现心跳文件",
  "no data": "暂无数据",
};

const formatLabel = (value) => {
  if (!value) return "未知";
  if (LABEL_MAP[value]) return LABEL_MAP[value];
  return value
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
};

const renderSkills = async () => {
  const data = await fetchJSON("/api/skills");
  if (!data) return;

  setText('[data-stat="skills-total"]', data.stats.total);
  setText('[data-stat="skills-custom"]', data.stats.custom);

  const list = document.querySelector('[data-role="skills-list"]');
  if (!list) return;
  list.innerHTML = "";

  const detail = document.querySelector('[data-role="skill-detail"]');
  const detailTitle = document.querySelector('[data-role="skill-detail-title"]');
  const detailPath = document.querySelector('[data-role="skill-detail-path"]');
  const detailContent = document.querySelector('[data-role="skill-detail-content"]');
  let activeSkill = null;
  let activeButton = null;

  const closeDetail = () => {
    if (!detail) return;
    detail.hidden = true;
    detailContent.textContent = "";
    activeSkill = null;
    if (activeButton) {
      activeButton.textContent = "查看";
      activeButton = null;
    }
  };

  if (detail) {
    detail.hidden = true;
  }

  data.items.forEach((skill) => {
    const card = document.createElement("article");
    card.className = "card";
    const badgeClass = skill.scope === "system" ? "sun" : "leaf";
    const docPath = skill.doc_path || skill.path;
    card.innerHTML = `
      <div class="badge ${badgeClass}">${formatLabel(skill.scope)}</div>
      <h3>${skill.name}</h3>
      <p>${skill.description || "暂无描述。"}</p>
      <div class="card-footer">
        <div class="chip">${skill.path}</div>
        <button class="button outline" data-path="${docPath}">查看</button>
      </div>
    `;
    list.appendChild(card);

    const button = card.querySelector("button");
    button.addEventListener("click", async () => {
      if (!detail) return;
      const isSame = activeSkill === skill.name && !detail.hidden;
      if (isSame) {
        closeDetail();
        return;
      }

      const result = await fetchJSON(`/api/skill?path=${encodeURIComponent(docPath)}`);
      detailTitle.textContent = skill.name;
      detailPath.textContent = docPath;
      if (result && result.content) {
        detailContent.textContent = result.content;
      } else {
        detailContent.textContent = "无法加载 SKILL.md，请确认本地服务运行正常。";
      }
      if (activeButton && activeButton !== button) {
        activeButton.textContent = "查看";
      }
      list.insertBefore(detail, card.nextSibling);
      detail.hidden = false;
      activeSkill = skill.name;
      activeButton = button;
      button.textContent = "收起";
    });
  });
};

const renderTodos = async () => {
  const data = await fetchJSON("/api/todos");
  if (!data) return;

  setText('[data-stat="todos-today"]', data.stats.today);
  setText('[data-stat="todos-week"]', data.stats.week);
  setText('[data-stat="todos-total"]', data.stats.total);

  const timeline = document.querySelector('[data-role="todo-timeline"]');
  if (!timeline) return;
  timeline.innerHTML = "";

  data.items.forEach((todo) => {
    const item = document.createElement("div");
    item.className = "timeline-item";
    const badgeClass =
      todo.status === "scheduled"
        ? "sun"
        : todo.status === "open"
          ? "sun"
          : "";
    item.innerHTML = `
      <div class="timeline-time">${formatDate(todo.due_at)}</div>
      <div>
        <div class="badge ${badgeClass}">${formatLabel(todo.status)}</div>
        <h3>${todo.title}</h3>
        <div class="timeline-meta">ID: ${todo.id || "-"} · 创建: ${formatDate(todo.created_at)}</div>
        <p>${formatLabel(todo.action_type)} · ${todo.action_summary || "-"}</p>
      </div>
    `;
    timeline.appendChild(item);
  });
};

const renderRuns = async () => {
  const data = await fetchJSON("/api/runs");
  if (!data) return;

  setText('[data-stat="runs-today"]', data.stats.today);
  setText('[data-stat="runs-success-rate"]', `${data.stats.success_rate}%`);
  setText('[data-stat="runs-failed"]', data.stats.failed);

  const table = document.querySelector('[data-role="runs-table"]');
  if (table) {
    table.innerHTML = "";
    data.items.forEach((run) => {
      const row = document.createElement("tr");
      const badgeClass = run.status === "success" ? "leaf" : run.status === "failed" ? "coral" : "sun";
      row.innerHTML = `
        <td>${run.id}</td>
        <td>${run.task}</td>
        <td>${formatDate(run.started_at)}</td>
        <td><span class="badge ${badgeClass}">${formatLabel(run.status)}</span></td>
      `;
      table.appendChild(row);
    });
  }

  const summary = document.querySelector('[data-role="runs-summary"]');
  if (summary) {
    const latest = data.items[0];
    const summaryText = latest
      ? `最新执行：${latest.task}，时间 ${formatDate(latest.started_at)}，状态 ${formatLabel(
          latest.status
        )}。`
      : "暂无执行数据。";
    summary.querySelector("p")?.remove();
    const p = document.createElement("p");
    p.textContent = summaryText;
    summary.insertBefore(p, summary.querySelector(".card-footer"));
  }
};

const renderAgent = async () => {
  const data = await fetchJSON("/api/agent");
  if (!data) {
    const info = document.querySelector('[data-role="agent-info"]');
    if (info) {
      info.innerHTML = '<div class="list-item">未获取到 Agent 数据，请先启动 instance_me/manage_service.py。</div>';
    }
    return;
  }

  setText('[data-stat="agent-status"]', formatLabel(data.status));
  const statusNote = STATUS_NOTE_MAP[data.status_note] || data.status_note || "-";
  setText('[data-stat="agent-status-badge"]', statusNote);
  setText('[data-stat="agent-last-run"]', formatDate(data.last_run));
  setText('[data-stat="agent-task-count"]', data.tasks.length);

  const info = document.querySelector('[data-role="agent-info"]');
  if (info) {
    info.innerHTML = "";
    const items = [
      { label: "Agent 名称", value: data.name },
      { label: "最近心跳", value: formatDate(data.heartbeat) },
      { label: "脚本路径", value: data.script_path },
      { label: "时区", value: data.timezone },
      { label: "日志文件", value: data.log_path },
    ];
    items.forEach((item) => {
      const row = document.createElement("div");
      row.className = "list-item";
      row.innerHTML = `
        <div class="list-title">${item.label}</div>
        <div>${item.value}</div>
      `;
      info.appendChild(row);
    });
  }

  const tasks = document.querySelector('[data-role="agent-tasks"]');
  if (tasks) {
    tasks.innerHTML = "";
    data.tasks.forEach((task) => {
      const row = document.createElement("div");
      row.className = "list-item";
      row.innerHTML = `
        <div class="list-title">${task.id}</div>
        <div>${formatLabel(task.type)} · ${task.schedule}</div>
        <div>下次运行：${formatDate(task.next_run)}</div>
      `;
      tasks.appendChild(row);
    });
  }

  const runs = document.querySelector('[data-role="agent-runs"]');
  if (runs) {
    runs.innerHTML = "";
  }
};

const init = () => {
  setupReveals();
  const page = document.body.dataset.page;
  if (page === "skills") renderSkills();
  if (page === "todos") {
    renderTodos();
    setInterval(renderTodos, 60 * 1000);
  }
  if (page === "runs") renderRuns();
  if (page === "agent") {
    renderAgent();
    setInterval(renderAgent, 60 * 1000);
  }
};

init();
