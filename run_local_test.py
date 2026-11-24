"""一键本地自测：启动服务 + 启动本机采集端 + 打开浏览器观看页面。

运行：
    .venv/Scripts/python.exe run_local_test.py

按 Ctrl+C 停止本次启动的进程。
"""

import os
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

BASE = Path(__file__).resolve().parent
PORT = 8000
TOKEN = "localtest"
AGENT_ID = "local-pc"
URL = f"http://127.0.0.1:{PORT}/?token={TOKEN}&agent={AGENT_ID}"


def health() -> dict:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=2) as response:
            import json
            return json.loads(response.read().decode())
    except Exception:
        return {}


def wait_for_server() -> None:
    deadline = time.time() + 20
    while time.time() < deadline:
        if health().get("ok"):
            return
        time.sleep(0.2)
    raise RuntimeError("服务启动超时")


def main() -> None:
    processes: list[subprocess.Popen] = []
    env = dict(os.environ, MONITOR_TOKEN=TOKEN)

    if not health().get("ok"):
        processes.append(
            subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "server:app", "--host", "127.0.0.1",
                 "--port", str(PORT), "--log-level", "warning"],
                cwd=BASE, env=env,
            )
        )
        print("[setup] 中转服务已启动")
    else:
        print("[setup] 复用已运行的中转服务")

    wait_for_server()

    online = [a.get("agent_id") for a in health().get("agents", [])]
    if AGENT_ID not in online:
        processes.append(
            subprocess.Popen(
                [sys.executable, "agent.py", "--server", f"ws://127.0.0.1:{PORT}",
                 "--token", TOKEN, "--agent-id", AGENT_ID,
                 "--fps", "5", "--quality", "55", "--max-width", "960"],
                cwd=BASE, env=env,
            )
        )
        print("[setup] 本机采集端已启动")
    else:
        print("[setup] 采集端已在线，跳过重复启动")

    print(f"[setup] 打开观看页面：{URL}")
    webbrowser.open(URL)
    print("[setup] 按 Ctrl+C 停止本次启动的进程")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[setup] 正在停止…")
        for process in processes:
            process.terminate()
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        print("[setup] 已停止")


if __name__ == "__main__":
    main()
