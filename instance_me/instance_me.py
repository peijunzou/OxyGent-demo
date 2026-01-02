import asyncio
import logging
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from oxygent import MAS, Config

from char_agent import build_oxy_space
from demo.point_util import PortManager
from scheduler_agent import POLL_INTERVAL_SECONDS, start_scheduler_in_thread

DEFAULT_PORT = 8080


async def main() -> None:
    # 启动调度器后台线程，负责代办扫描与定时任务。
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    start_scheduler_in_thread(poll_interval=POLL_INTERVAL_SECONDS)

    # 启动 OxyGent Web 服务，提供对话入口。
    Config.load_from_json(str(ROOT_DIR / "config.json"))
    port_value = os.getenv("INSTANCE_ME_PORT") or os.getenv("OXYGENT_PORT")
    port = int(port_value) if port_value else DEFAULT_PORT
    Config.set_module_config("server", "port", port)
    port_manager = PortManager()
    port_manager.ensure_port_available(port)
    async with MAS(oxy_space=build_oxy_space()) as mas:
        await mas.start_web_service(first_query="你好，我是 Instance Me Agent，可以帮你新增/修改/关闭代办任务。")


if __name__ == "__main__":
    asyncio.run(main())
