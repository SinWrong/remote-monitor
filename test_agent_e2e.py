"""端到端测试：真实采集一帧屏幕，经中转服务送到观看端。"""

import asyncio
import base64
import io
import json
import os
import subprocess
import sys
import time
import urllib.request

from PIL import Image

PORT = 8766
TOKEN = "e2e-test-token"


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
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "server:app", "--host", "127.0.0.1",
         "--port", str(PORT), "--log-level", "warning"],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    agent = None
    try:
        await wait_for_health()
        agent = subprocess.Popen(
            [sys.executable, "agent.py", "--server", f"ws://127.0.0.1:{PORT}",
             "--token", TOKEN, "--agent-id", "pc1", "--fps", "2",
             "--quality", "30", "--max-width", "640"],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        await asyncio.sleep(1.5)

        import websockets
        uri = f"ws://127.0.0.1:{PORT}/ws/viewer?token={TOKEN}&agent_id=pc1"
        async with websockets.connect(uri) as viewer:
            deadline = time.time() + 15
            frame = None
            while time.time() < deadline and frame is None:
                message = json.loads(await asyncio.wait_for(viewer.recv(), timeout=10))
                if message.get("type") == "frame":
                    frame = message
            assert frame is not None, "未收到采集端画面"
            image_bytes = base64.b64decode(frame["data"])
            image = Image.open(io.BytesIO(image_bytes))
            assert image.width <= 640, image.size
            print(f"e2e test passed: received {image.size} frame, {len(image_bytes)} bytes")
    finally:
        if agent is not None:
            agent.terminate()
            try:
                agent.wait(timeout=5)
            except subprocess.TimeoutExpired:
                agent.kill()
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()


if __name__ == "__main__":
    asyncio.run(main())
