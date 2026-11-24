"""检查本地演示是否真的有画面在流转。"""

import asyncio
import json
import urllib.request

import websockets

PORT = 8000
TOKEN = "localtest"
AGENT = "local-pc"


async def main() -> None:
    health = urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=5).read().decode()
    print("health:", health)

    uri = f"ws://127.0.0.1:{PORT}/ws/viewer?token={TOKEN}&agent_id={AGENT}"
    async with websockets.connect(uri) as viewer:
        while True:
            message = json.loads(await asyncio.wait_for(viewer.recv(), timeout=10))
            if message.get("type") == "frame":
                print(f"frame: {message['w']}x{message['h']}, base64 chars: {len(message['data'])}")
                return


if __name__ == "__main__":
    asyncio.run(main())
