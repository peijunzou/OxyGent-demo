import json
import os
import re
from datetime import datetime, timedelta
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


ROOT_DIR = Path(__file__).resolve().parents[1]
INSTANCE_DIR = ROOT_DIR / "instance_me"
UI_DIR = INSTANCE_DIR / "ui"
LOCAL_FILE_DIR = INSTANCE_DIR / "local_file"
CACHE_DIR = INSTANCE_DIR / "cache_dir"

CODEX_HOME = Path(os.getenv("CODEX_HOME", Path.home() / ".codex"))
SKILLS_DIR = CODEX_HOME / "skills"

AGENT_SCRIPT = INSTANCE_DIR / "23_personal_agent.py"
LOG_PATH = LOCAL_FILE_DIR / "agent_log.txt"
TASKS_PATH = LOCAL_FILE_DIR / "agent_tasks.json"
TODOS_PATH = LOCAL_FILE_DIR / "todos.json"
STATE_PATH = CACHE_DIR / "personal_agent_state.json"
HEARTBEAT_PATH = CACHE_DIR / "agent_heartbeat.json"

RUN_SUCCESS_RE = re.compile(r"^\[(?P<ts>[^\]]+)\] 任务完成: (?P<task>.+)$")
RUN_FAIL_RE = re.compile(r"^\[(?P<ts>[^\]]+)\] 任务失败: (?P<task>[^,]+)")


def read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def parse_front_matter(text: str) -> dict:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    data = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = value.strip()
    return data


def list_skills() -> dict:
    items = []
    if not SKILLS_DIR.exists():
        return {"items": [], "stats": {"total": 0, "system": 0, "custom": 0}}

    for skill_file in SKILLS_DIR.rglob("SKILL.md"):
        try:
            content = skill_file.read_text(encoding="utf-8")
        except OSError:
            continue
        meta = parse_front_matter(content)
        name = meta.get("name") or skill_file.parent.name
        description = meta.get("description", "")
        scope = "system" if ".system" in skill_file.parts else "custom"
        items.append(
            {
                "name": name,
                "description": description,
                "path": str(skill_file.parent),
                "doc_path": str(skill_file),
                "scope": scope,
            }
        )

    items.sort(key=lambda item: item["name"].lower())
    stats = {
        "total": len(items),
        "system": sum(1 for item in items if item["scope"] == "system"),
        "custom": sum(1 for item in items if item["scope"] == "custom"),
    }
    return {"items": items, "stats": stats}


def load_todos() -> dict:
    todos = read_json(TODOS_PATH, [])
    if not isinstance(todos, list):
        todos = []
    tasks = read_json(TASKS_PATH, [])
    if not isinstance(tasks, list):
        tasks = []
    now = datetime.now()
    today = now.date()
    week_end = today + timedelta(days=7)

    def parse_due(item):
        try:
            return datetime.strptime(item.get("due_at", ""), "%Y-%m-%d %H:%M")
        except ValueError:
            return None

    open_today = 0
    open_week = 0
    scheduled_count = 0
    timeline = []

    for item in todos:
        status = item.get("status", "open")
        due_at = parse_due(item)
        if status == "done":
            continue
        action = item.get("action", {})
        action_summary = action.get("message", "")
        repo_path = action.get("repo_path")
        if repo_path:
            action_summary = f"{action_summary} 仓库: {repo_path}".strip()
        timeline.append(
            {
                "title": item.get("title", "todo"),
                "due_at": item.get("due_at"),
                "status": status,
                "action_type": action.get("type", "note"),
                "action_summary": action_summary,
                "source": "todo",
            }
        )

    for task in tasks:
        if not task.get("enabled", True):
            continue
        schedule = task.get("schedule", {})
        next_run = next_run_time(schedule)
        if not next_run:
            continue
        todo_meta = task.get("todo", {}) if isinstance(task.get("todo"), dict) else {}
        title = todo_meta.get("title") or task.get("id", "task")
        action_type = todo_meta.get("action", {}).get("type") or task.get("type", "task")
        action_summary = schedule_label(schedule)
        timeline.append(
            {
                "title": title,
                "due_at": next_run,
                "status": "scheduled",
                "action_type": action_type,
                "action_summary": action_summary,
                "source": "schedule",
            }
        )

    timeline.sort(key=lambda item: item.get("due_at") or "")

    open_today = 0
    open_week = 0
    scheduled_count = 0
    for item in timeline:
        if item.get("status") in {"done"}:
            continue
        try:
            due_dt = datetime.fromisoformat(item.get("due_at").replace(" ", "T"))
        except Exception:
            continue
        if item.get("status") == "scheduled":
            scheduled_count += 1
        if due_dt.date() == today:
            open_today += 1
        if today <= due_dt.date() <= week_end:
            open_week += 1

    return {
        "items": timeline[:10],
        "stats": {"today": open_today, "week": open_week, "scheduled": scheduled_count},
    }


def parse_runs() -> dict:
    runs = []
    if not LOG_PATH.exists():
        return {"items": [], "stats": {"today": 0, "failed": 0, "success_rate": 0}}

    for line in LOG_PATH.read_text(encoding="utf-8").splitlines():
        success_match = RUN_SUCCESS_RE.match(line)
        fail_match = RUN_FAIL_RE.match(line)
        if success_match:
            runs.append(
                {
                    "task": success_match.group("task"),
                    "started_at": success_match.group("ts"),
                    "status": "success",
                }
            )
        elif fail_match:
            runs.append(
                {
                    "task": fail_match.group("task"),
                    "started_at": fail_match.group("ts"),
                    "status": "failed",
                }
            )

    runs.reverse()
    today = datetime.now().date().isoformat()
    runs_today = [run for run in runs if run["started_at"].startswith(today)]
    success_count = sum(1 for run in runs_today if run["status"] == "success")
    failed_count = sum(1 for run in runs_today if run["status"] == "failed")
    success_rate = int((success_count / len(runs_today)) * 100) if runs_today else 0

    items = []
    for idx, run in enumerate(runs[:15]):
        run_id = f"run-{len(runs) - idx:04d}"
        items.append(
            {
                "id": run_id,
                "task": run["task"],
                "started_at": run["started_at"],
                "status": run["status"],
            }
        )

    return {
        "items": items,
        "stats": {"today": len(runs_today), "failed": failed_count, "success_rate": success_rate},
    }


def schedule_label(schedule: dict) -> str:
    kind = schedule.get("kind", "once")
    if kind == "weekly":
        day_map = {
            "mon": "周一",
            "tue": "周二",
            "wed": "周三",
            "thu": "周四",
            "fri": "周五",
            "sat": "周六",
            "sun": "周日",
        }
        day_label = day_map.get(str(schedule.get("day_of_week", "")).lower(), "周-")
        return f"{day_label} {schedule.get('time', '-')}"
    if kind == "daily":
        return f"每天 {schedule.get('time', '-')}"
    if kind == "once":
        return "一次性"
    return "未知"


def next_run_time(schedule: dict) -> str:
    time_str = schedule.get("time")
    if not time_str:
        return ""
    now = datetime.now()
    try:
        hour, minute = map(int, time_str.split(":"))
    except ValueError:
        return ""

    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if schedule.get("kind") == "daily":
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate.isoformat()

    if schedule.get("kind") == "weekly":
        day_map = {
            "mon": 0,
            "tue": 1,
            "wed": 2,
            "thu": 3,
            "fri": 4,
            "sat": 5,
            "sun": 6,
        }
        target = day_map.get(str(schedule.get("day_of_week", "")).lower())
        if target is None:
            return ""
        days_ahead = (target - now.weekday()) % 7
        if days_ahead == 0 and candidate <= now:
            days_ahead = 7
        candidate = candidate + timedelta(days=days_ahead)
        return candidate.isoformat()

    return ""


def load_agent() -> dict:
    tasks = read_json(TASKS_PATH, [])
    if not isinstance(tasks, list):
        tasks = []

    state = read_json(STATE_PATH, {})
    last_run = None
    if isinstance(state, dict):
        times = []
        for value in state.values():
            try:
                times.append(datetime.fromisoformat(value))
            except (TypeError, ValueError):
                continue
        if times:
            last_run = max(times).isoformat()

    status = "unknown"
    status_note = "no heartbeat"
    heartbeat = read_json(HEARTBEAT_PATH, {})
    heartbeat_ts = heartbeat.get("timestamp") if isinstance(heartbeat, dict) else None

    def compute_delta(timestamp: str):
        try:
            dt = datetime.fromisoformat(timestamp)
        except ValueError:
            return None
        now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
        return now - dt

    if heartbeat_ts:
        delta = compute_delta(heartbeat_ts)
        if delta is not None:
            if delta <= timedelta(minutes=2):
                status = "active"
                status_note = "heartbeat ok"
            else:
                status = "idle"
                status_note = "no recent heartbeat"
    elif last_run:
        status = "idle"
        status_note = "no heartbeat file"

    tasks_info = []
    for task in tasks:
        tasks_info.append(
            {
                "id": task.get("id", "unknown"),
                "type": task.get("type", "unknown"),
                "schedule": schedule_label(task.get("schedule", {})),
                "next_run": next_run_time(task.get("schedule", {})),
            }
        )

    runs = parse_runs()["items"][:5]
    return {
        "name": "Personal Agent",
        "status": status,
        "status_note": status_note,
        "last_run": last_run,
        "heartbeat": heartbeat_ts,
        "timezone": "Asia/Shanghai",
        "script_path": str(AGENT_SCRIPT),
        "log_path": str(LOG_PATH),
        "tasks": tasks_info,
        "recent_runs": runs,
    }


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT_DIR), **kwargs)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self.send_response(302)
            self.send_header("Location", "/instance_me/ui/skills.html")
            self.end_headers()
            return

        if parsed.path.startswith("/api/"):
            self.handle_api(parsed)
            return

        return super().do_GET()

    def handle_api(self, parsed):
        path = parsed.path
        if path == "/api/skills":
            payload = list_skills()
        elif path == "/api/todos":
            payload = load_todos()
        elif path == "/api/runs":
            payload = parse_runs()
        elif path == "/api/agent":
            payload = load_agent()
        elif path == "/api/skill":
            payload = self.read_skill_doc(parsed.query)
        else:
            self.send_response(404)
            self.end_headers()
            return

        data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def read_skill_doc(self, query: str):
        params = parse_qs(query)
        raw_path = params.get("path", [""])[0]
        if not raw_path:
            return {"error": "missing path"}
        doc_path = Path(raw_path).expanduser().resolve()
        try:
            doc_path.relative_to(SKILLS_DIR.resolve())
        except ValueError:
            return {"error": "path not allowed"}
        if not doc_path.is_file() or doc_path.name != "SKILL.md":
            return {"error": "invalid skill doc"}
        try:
            content = doc_path.read_text(encoding="utf-8")
        except OSError:
            return {"error": "failed to read"}
        return {"path": str(doc_path), "content": content}


def run():
    server = ThreadingHTTPServer(("127.0.0.1", 8082), Handler)
    print("Server running at http://127.0.0.1:8082")
    server.serve_forever()


if __name__ == "__main__":
    run()
