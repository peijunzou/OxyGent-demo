import json
import os
from pathlib import Path
from typing import Any, Dict, List

ROOT_DIR = Path(__file__).resolve().parents[1]
TODOS_PATH = ROOT_DIR / "local_file" / "todos.json"
TASKS_PATH = ROOT_DIR / "local_file" / "agent_tasks.json"
ENV_PATH = ROOT_DIR.parent / ".env"


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_env_file(path: Path) -> None:
    # 兼容本地 .env 配置，避免环境变量缺失导致启动失败。
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def ensure_todos() -> List[Dict[str, Any]]:
    data = load_json(TODOS_PATH, [])
    return data if isinstance(data, list) else []


def ensure_tasks() -> List[Dict[str, Any]]:
    data = load_json(TASKS_PATH, [])
    return data if isinstance(data, list) else []
