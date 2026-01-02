import argparse
import json
import logging
import os
import subprocess
import threading
import time
from datetime import datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo


ROOT_DIR = Path(__file__).resolve().parent
# 任务清单与代办存放在 local_file，方便直接编辑。
TASKS_PATH = ROOT_DIR / "local_file" / "agent_tasks.json"
TODOS_PATH = ROOT_DIR / "local_file" / "todos.json"
# 记录每个任务的最后执行时间，避免一天内重复执行。
STATE_PATH = ROOT_DIR / "cache_dir" / "personal_agent_state.json"
# 心跳文件用于监控 Agent 是否在运行。
HEARTBEAT_PATH = ROOT_DIR / "cache_dir" / "agent_heartbeat.json"
# 运行结果写入日志，便于人工查看。
LOG_PATH = ROOT_DIR / "local_file" / "agent_log.txt"

DEFAULT_TIMEZONE = os.getenv("AGENT_TIMEZONE", "Asia/Shanghai")
POLL_INTERVAL_SECONDS = int(os.getenv("AGENT_POLL_INTERVAL_SECONDS", "60"))

WEEKDAY_MAP = {
    "mon": 0,
    "tue": 1,
    "wed": 2,
    "thu": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6,
}


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logging.error("Failed to parse JSON: %s", path)
        return default


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_timezone() -> ZoneInfo:
    try:
        return ZoneInfo(DEFAULT_TIMEZONE)
    except Exception:
        logging.warning("Invalid timezone %s, falling back to local time", DEFAULT_TIMEZONE)
        return ZoneInfo("Asia/Shanghai")


def parse_time(value: Optional[str]) -> Optional[dt_time]:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%H:%M").time()
    except ValueError:
        logging.error("Invalid time format: %s (expected HH:MM)", value)
        return None


def parse_datetime(value: Optional[str], tz: ZoneInfo) -> Optional[datetime]:
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=tz)
        except ValueError:
            continue
    logging.error("Invalid datetime format: %s", value)
    return None


def last_run_date(last_run: Optional[str]) -> Optional[datetime.date]:
    if not last_run:
        return None
    try:
        return datetime.fromisoformat(last_run).date()
    except ValueError:
        return None


def append_log(message: str) -> None:
    timestamp = datetime.now(get_timezone()).strftime("%Y-%m-%d %H:%M:%S")
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"[{timestamp}] {message}\n")


def append_log_block(title: str, content: str) -> None:
    # 将多行输出写入日志，保留标题与结束标记。
    timestamp = datetime.now(get_timezone()).strftime("%Y-%m-%d %H:%M:%S")
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"[{timestamp}] {title}\n")
        if content:
            handle.write(content.rstrip() + "\n")
        handle.write(f"[{timestamp}] --- end ---\n")


def run_shell(command: str, workdir: Optional[str] = None, args: Optional[List[str]] = None) -> str:
    # 兼容传入 shell 字符串或 argv 列表。
    if args:
        cmd = [command] + args
        shell = False
    else:
        cmd = command
        shell = True
    result = subprocess.run(
        cmd,
        shell=shell,
        cwd=workdir,
        check=False,
        capture_output=True,
        text=True,
    )
    output = (result.stdout or "").strip()
    error = (result.stderr or "").strip()
    if result.returncode != 0:
        raise RuntimeError(f"Command failed ({result.returncode}): {error or output}")
    return output or "Command finished without output."


def run_xingyun_tag_check(config: Dict[str, Any]) -> str:
    # 调用 code X 中的行云卡片标签检查脚本。
    repo_path = config.get("repo_path") or os.getenv("XINGYUN_REPO_PATH")
    if not repo_path:
        raise RuntimeError("Missing XINGYUN_REPO_PATH for Xingyun tag check.")

    repo = Path(repo_path)
    script_path = repo / "scripts" / "run-req-card-tag.sh"
    if not script_path.exists():
        raise RuntimeError(f"Missing script: {script_path}")

    required_dir = repo / "src/main/java/com/jd/gyl/webmagic/datasource/xingyun/requirementmanage"
    if not (required_dir / "login.properties").exists():
        raise RuntimeError("Missing login.properties under requirementmanage.")
    if not (required_dir / "public_key.pem").exists():
        raise RuntimeError("Missing public_key.pem under requirementmanage.")

    args = [str(script_path)]
    if config.get("test_mode"):
        args.append("test")

    return run_shell(args[0], workdir=str(repo), args=args[1:])


def run_changan_workorder_check(config: Dict[str, Any]) -> str:
    # 调用长安工单检查脚本。
    repo_path = config.get("repo_path") or os.getenv("CHANGAN_REPO_PATH")
    if not repo_path:
        raise RuntimeError("Missing CHANGAN_REPO_PATH for Changan work order check.")

    repo = Path(repo_path)
    if not repo.exists():
        raise RuntimeError(f"Changan repo not found: {repo_path}")

    args = ["run", "./cmd/changan-workorder-check"]
    if config.get("test_mode"):
        args.append("test")

    return run_shell("go", workdir=str(repo), args=args)


def run_todo_scan(now: datetime) -> str:
    # 扫描到期代办并执行，完成后写回 todos.json。
    todos = load_json(TODOS_PATH, [])
    if not isinstance(todos, list):
        raise RuntimeError("todos.json should be a list of todo items.")

    updated = False
    for todo in todos:
        if todo.get("status") == "done":
            continue
        due_at = parse_datetime(todo.get("due_at"), now.tzinfo)
        if due_at is None or due_at > now:
            continue

        action = todo.get("action", {})
        action_type = action.get("type", "note")
        title = todo.get("title", "todo")
        try:
            if action_type == "xingyun_tag_check":
                result = run_xingyun_tag_check(action)
            elif action_type == "changan_workorder_check":
                result = run_changan_workorder_check(action)
            elif action_type == "shell":
                result = run_shell(
                    action.get("command", ""),
                    workdir=action.get("workdir"),
                    args=action.get("args"),
                )
            else:
                # 默认动作：写入日志，确保代办不丢失。
                message = action.get("message") or title
                append_log(f"TODO: {message}")
                result = "Logged todo action."

            todo["status"] = "done"
            todo["done_at"] = now.isoformat()
            todo["result"] = result
            append_log_block(f"业务任务完成: {title}", result)
            updated = True
        except Exception as exc:
            todo["last_error"] = str(exc)
            todo["last_attempt_at"] = now.isoformat()
            append_log(f"业务任务失败: {title}, 错误: {exc}")
            updated = True

    if updated:
        save_json(TODOS_PATH, todos)
    return "Todo scan finished."


def create_todo(task: Dict[str, Any], now: datetime) -> str:
    # 创建代办任务条目，用于后续扫描执行。
    todo_config = task.get("todo", {})
    if not isinstance(todo_config, dict):
        raise RuntimeError("todo_create 任务缺少 todo 配置。")

    title = todo_config.get("title", "todo")
    action = todo_config.get("action") or {"type": "note", "message": title}
    due_at = todo_config.get("due_at")
    due_offset = int(todo_config.get("due_offset_minutes", 0))
    if not due_at:
        due_at = (now + timedelta(minutes=due_offset)).strftime("%Y-%m-%d %H:%M")

    todo_id_prefix = todo_config.get("id_prefix") or task.get("id", "todo")
    todo_id = f"{todo_id_prefix}-{now.strftime('%Y%m%d%H%M%S')}"
    todo_item = {
        "id": todo_id,
        "title": title,
        "due_at": due_at,
        "status": "open",
        "action": action,
    }

    todos = load_json(TODOS_PATH, [])
    if not isinstance(todos, list):
        todos = []
    todos.append(todo_item)
    save_json(TODOS_PATH, todos)
    return f"Todo created: {title} @ {due_at}"


def run_task(task: Dict[str, Any], now: datetime) -> str:
    task_type = task.get("type")
    if task_type == "todo_scan":
        return run_todo_scan(now)
    if task_type == "todo_create":
        return create_todo(task, now)
    if task_type == "xingyun_tag_check":
        return run_xingyun_tag_check(task)
    if task_type == "changan_workorder_check":
        return run_changan_workorder_check(task)
    raise RuntimeError(f"Unsupported task type: {task_type}")


class TaskScheduler:
    def __init__(self, tz: ZoneInfo, poll_interval: int):
        self.tz = tz
        self.poll_interval = poll_interval
        self.skip_notices = set()

    def write_heartbeat(self, now: datetime) -> None:
        # 写入心跳时间，供监控页面读取。
        save_json(HEARTBEAT_PATH, {"timestamp": now.isoformat()})

    def load_tasks(self) -> List[Dict[str, Any]]:
        tasks = load_json(TASKS_PATH, [])
        if not isinstance(tasks, list):
            logging.error("agent_tasks.json should be a list of tasks.")
            return []
        return tasks

    def load_state(self) -> Dict[str, str]:
        state = load_json(STATE_PATH, {})
        return state if isinstance(state, dict) else {}

    def evaluate_task(
        self, task: Dict[str, Any], now: datetime, state: Dict[str, str]
    ) -> Tuple[bool, str, Optional[dt_time]]:
        schedule = task.get("schedule", {})
        kind = schedule.get("kind")
        if not kind:
            return False, "missing schedule", None

        task_id = task.get("id")
        last_run_at = state.get(task_id)

        if kind == "interval":
            minutes = schedule.get("minutes")
            try:
                minutes = int(minutes)
            except (TypeError, ValueError):
                return False, "missing interval", None
            if last_run_at:
                try:
                    last_run_dt = datetime.fromisoformat(last_run_at)
                    if now - last_run_dt < timedelta(minutes=minutes):
                        return False, "interval not due yet", None
                except ValueError:
                    pass
            return True, "interval due", None

        run_time = parse_time(schedule.get("time"))
        if run_time is None:
            return False, "missing schedule time", run_time

        last_run = last_run_date(last_run_at)
        if last_run == now.date():
            return False, "already ran today", run_time

        if kind == "daily":
            return now.time() >= run_time, "not due yet", run_time

        if kind == "weekly":
            day_of_week = str(schedule.get("day_of_week", "")).lower()
            target_day = WEEKDAY_MAP.get(day_of_week)
            if target_day is None:
                logging.error("Invalid day_of_week for task %s: %s", task_id, day_of_week)
                return False, "invalid schedule", run_time
            should_run = now.weekday() == target_day and now.time() >= run_time
            return should_run, "not due yet", run_time

        logging.error("Unsupported schedule kind for task %s: %s", task_id, kind)
        return False, "unsupported schedule", run_time

    def run_cycle(self) -> None:
        now = datetime.now(self.tz)
        self.write_heartbeat(now)
        tasks = self.load_tasks()
        state = self.load_state()
        state_changed = False

        for task in tasks:
            if not task.get("enabled", True):
                task_id = task.get("id", "unknown")
                notice_key = (task_id, now.date().isoformat(), "disabled")
                if notice_key not in self.skip_notices:
                    append_log(f"任务跳过: {task_id}, 原因: 已禁用")
                    self.skip_notices.add(notice_key)
                continue
            task_id = task.get("id")
            if not task_id:
                logging.warning("Task missing id: %s", task)
                continue
            should_run, reason, run_time = self.evaluate_task(task, now, state)
            if should_run:
                logging.info("Running task: %s", task_id)
                try:
                    result = run_task(task, now)
                    append_log_block(f"任务完成: {task_id}", result)
                    state[task_id] = now.isoformat()
                    state_changed = True
                except Exception as exc:
                    logging.error("Task %s failed: %s", task_id, exc)
                    append_log(f"任务失败: {task_id}, 错误: {exc}")
            else:
                if reason == "already ran today":
                    notice_key = (task_id, now.date().isoformat(), reason)
                    if notice_key not in self.skip_notices:
                        append_log(
                            f"任务跳过: {task_id}, 原因: 今日已执行过, 时间: {run_time}"
                        )
                        self.skip_notices.add(notice_key)

        if state_changed:
            save_json(STATE_PATH, state)

    def run(self, once: bool) -> None:
        while True:
            self.run_cycle()
            if once:
                break
            time.sleep(self.poll_interval)


def start_scheduler_in_thread(tz: Optional[ZoneInfo] = None, poll_interval: int = 60) -> TaskScheduler:
    # 通过后台线程运行调度器，避免阻塞主进程。
    scheduler = TaskScheduler(tz=tz or get_timezone(), poll_interval=poll_interval)
    thread = threading.Thread(target=scheduler.run, args=(False,), daemon=True)
    thread.start()
    return scheduler


def main() -> None:
    parser = argparse.ArgumentParser(description="Instance Me Scheduler Agent")
    parser.add_argument("--once", action="store_true", help="run one cycle then exit")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    tz = get_timezone()
    logging.info("Scheduler agent started (%s)", tz.key)
    scheduler = TaskScheduler(tz=tz, poll_interval=POLL_INTERVAL_SECONDS)
    scheduler.run(args.once)


if __name__ == "__main__":
    main()
