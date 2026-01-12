import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

ROOT_DIR = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT_DIR / "config.json"


def _load_config() -> Dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _get_env_name() -> str:
    env_name = os.getenv("APP_ENV", "default").strip()
    return env_name or "default"


def _get_config_scope(config: Dict[str, Any], env_name: str) -> Dict[str, Any]:
    scope = config.get(env_name)
    return scope if isinstance(scope, dict) else {}


def _get_config_value(keys: list[str]) -> Optional[str]:
    config = _load_config()
    env_name = _get_env_name()
    for scope in (_get_config_scope(config, env_name), _get_config_scope(config, "default")):
        value: Any = scope
        for key in keys:
            if not isinstance(value, dict):
                value = None
                break
            value = value.get(key)
        if isinstance(value, str):
            value = value.strip()
            if value:
                return value
    return None


def get_repo_path_from_config(action_type: str) -> Optional[str]:
    key_map = {
        "xingyun_tag_check": "xingyun_repo_path",
        "changan_workorder_check": "changan_repo_path",
    }
    config_key = key_map.get(action_type)
    if not config_key:
        return None
    return _get_config_value(["instance_me", config_key])
