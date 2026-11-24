"""本地回环冒烟测试：验证采集端帧和控制指令都能正确中转。"""

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request

PORT = 8765
TOKEN = "smoke-test-token"


def free_port() -> int:
    return PORT


async def wait_for_health() -> None:
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=1) as response:
                if response.status == 200:
                    return
        except Exception:
            await asyncio.sleep(0.2)
    raise RuntimeError("服务启动超时")


async def main() -> None:
    env = dict(os.environ, MONITOR_TOKEN=TOKEN)
    process = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", "server:app",
            "--host", "127.0.0.1", "--port", str(free_port()), "--log-level", "warning",
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        await wait_for_health()
        import websockets

        agent_uri = f"ws://127.0.0.1:{PORT}/ws/agent?token={TOKEN}&agent_id=pc1&name=TestPC"
        viewer_uri = f"ws://127.0.0.1:{PORT}/ws/viewer?token={TOKEN}&agent_id=pc1"

        async with websockets.connect(agent_uri) as agent, websockets.connect(viewer_uri) as viewer:
            hello = json.loads(await asyncio.wait_for(viewer.recv(), timeout=5))
            assert hello["type"] == "agent_list" and hello["agents"][0]["name"] == "TestPC"

            frame = {"type": "frame", "seq": 1, "ts": 123, "w": 2, "h": 2, "data": "AAAA"}
            await agent.send(json.dumps(frame))
            received = json.loads(await asyncio.wait_for(viewer.recv(), timeout=5))
            assert received["data"] == "AAAA" and received["w"] == 2

            await viewer.send(json.dumps({"type": "config", "fps": 5, "quality": 40, "max_width": 640}))
            command = json.loads(await asyncio.wait_for(agent.recv(), timeout=5))
            assert command["type"] == "config" and command["fps"] == 5

        print("smoke test passed: frame relay and config relay both work")
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


if __name__ == "__main__":
    asyncio.run(main())
