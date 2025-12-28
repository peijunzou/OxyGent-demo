import argparse
import json
import logging
import os
import subprocess
import time
from datetime import datetime, time as dt_time
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo


ROOT_DIR = Path(__file__).resolve().parent
# 任务清单与代办存放在 local_file，方便直接编辑。
TASKS_PATH = ROOT_DIR / "local_file" / "agent_tasks.json"
TODOS_PATH = ROOT_DIR / "local_file" / "todos.json"
# 记录每个任务的最后执行时间，避免一天内重复执行。
STATE_PATH = ROOT_DIR / "cache_dir" / "personal_agent_state.json"
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


def should_run_task(task: Dict[str, Any], now: datetime, state: Dict[str, str]) -> bool:
    schedule = task.get("schedule", {})
    kind = schedule.get("kind")
    run_time = parse_time(schedule.get("time"))
    if not kind or run_time is None:
        return False

    task_id = task.get("id")
    last_run = last_run_date(state.get(task_id))
    if last_run == now.date():
        return False

    if kind == "daily":
        return now.time() >= run_time

    if kind == "weekly":
        day_of_week = str(schedule.get("day_of_week", "")).lower()
        target_day = WEEKDAY_MAP.get(day_of_week)
        if target_day is None:
            logging.error("Invalid day_of_week for task %s: %s", task_id, day_of_week)
            return False
        return now.weekday() == target_day and now.time() >= run_time

    logging.error("Unsupported schedule kind for task %s: %s", task_id, kind)
    return False


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
        if action_type == "xingyun_tag_check":
            result = run_xingyun_tag_check(action)
        elif action_type == "shell":
            result = run_shell(
                action.get("command", ""),
                workdir=action.get("workdir"),
                args=action.get("args"),
            )
        else:
            # 默认动作：写入日志，确保代办不丢失。
            message = action.get("message") or todo.get("title", "todo")
            append_log(f"TODO: {message}")
            result = "Logged todo action."

        todo["status"] = "done"
        todo["done_at"] = now.isoformat()
        todo["result"] = result
        updated = True

    if updated:
        save_json(TODOS_PATH, todos)
    return "Todo scan finished."


def run_task(task: Dict[str, Any], now: datetime) -> str:
    task_type = task.get("type")
    if task_type == "todo_scan":
        return run_todo_scan(now)
    if task_type == "xingyun_tag_check":
        return run_xingyun_tag_check(task)
    raise RuntimeError(f"Unsupported task type: {task_type}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Personal agent scheduler")
    parser.add_argument("--once", action="store_true", help="run one cycle then exit")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    tz = get_timezone()
    logging.info("Personal agent scheduler started (%s)", tz.key)

    while True:
        now = datetime.now(tz)
        tasks = load_json(TASKS_PATH, [])
        if not isinstance(tasks, list):
            logging.error("agent_tasks.json should be a list of tasks.")
            tasks = []
        state = load_json(STATE_PATH, {})
        state_changed = False

        for task in tasks:
            if not task.get("enabled", True):
                continue
            task_id = task.get("id")
            if not task_id:
                logging.warning("Task missing id: %s", task)
                continue
            if should_run_task(task, now, state):
                logging.info("Running task: %s", task_id)
                try:
                    result = run_task(task, now)
                    append_log_block(f"任务完成: {task_id}", result)
                    state[task_id] = now.isoformat()
                    state_changed = True
                except Exception as exc:
                    logging.error("Task %s failed: %s", task_id, exc)
                    append_log(f"任务失败: {task_id}, 错误: {exc}")

        if state_changed:
            save_json(STATE_PATH, state)

        if args.once:
            break
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
