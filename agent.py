"""Windows 屏幕采集端：定时截屏并推送到中转服务。

仅用于你自己拥有或已获得明确授权的设备。程序启动后会在终端持续显示运行提示。

示例：
    python agent.py --server wss://your-domain.com --token 你的密钥 --agent-id office-pc

查看显示器编号：
    python agent.py --list-monitors
"""

import argparse
import asyncio
import base64
import io
import json
import time
from urllib.parse import quote, urlencode

import mss
import websockets
from PIL import Image

MSS = getattr(mss, "MSS", mss.mss)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Windows 屏幕采集端")
    parser.add_argument("--server", default="ws://127.0.0.1:8000", help="中转服务地址，例如 wss://example.com")
    parser.add_argument("--token", default="", help="与服务端 MONITOR_TOKEN 一致")
    parser.add_argument("--agent-id", default="default", help="本设备标识，观看端需用相同 ID")
    parser.add_argument("--name", default="", help="设备显示名称")
    parser.add_argument("--monitor", type=int, default=1, help="显示器编号：0 表示所有屏幕合并，1 表示主屏")
    parser.add_argument("--fps", type=float, default=10.0, help="初始帧率，范围 1-30")
    parser.add_argument("--quality", type=int, default=65, help="初始 JPEG 质量，范围 20-95")
    parser.add_argument("--max-width", type=int, default=1280, help="初始最大宽度，范围 640-1920")
    parser.add_argument("--list-monitors", action="store_true", help="列出显示器编号后退出")
    return parser.parse_args()


def list_monitors() -> None:
    with MSS() as sct:
        for index, monitor in enumerate(sct.monitors):
            label = "all" if index == 0 else f"#{index}"
            width = monitor.get("width", monitor.get("w"))
            height = monitor.get("height", monitor.get("h"))
            print(f"{label}: {width}x{height} at ({monitor.get('left')}, {monitor.get('top')})")


def build_uri(args: argparse.Namespace) -> str:
    server = args.server.strip().rstrip("/")
    if not server.startswith(("ws://", "wss://")):
        server = "ws://" + server
    query = urlencode(
        {
            "token": args.token,
            "agent_id": args.agent_id,
            "name": args.name or args.agent_id,
        }
    )
    separator = "&" if "?" in server else "?"
    return f"{server}/ws/agent{separator}{query}"


def build_frame(sct: MSS, monitor: dict, state: dict) -> dict:
    shot = sct.grab(monitor)
    image = Image.frombytes("RGB", shot.size, shot.rgb)

    max_width = int(state["max_width"])
    if image.width > max_width:
        height = max(1, round(image.height * max_width / image.width))
        image = image.resize((max_width, height), Image.Resampling.BILINEAR)

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=int(state["quality"]))
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")

    state["seq"] += 1
    return {
        "type": "frame",
        "seq": state["seq"],
        "ts": int(time.time() * 1000),
        "w": image.width,
        "h": image.height,
        "data": encoded,
    }


def apply_config(state: dict, message: dict) -> None:
    if "fps" in message:
        state["fps"] = min(30.0, max(1.0, float(message["fps"])))
    if "quality" in message:
        state["quality"] = min(95, max(20, int(message["quality"])))
    if "max_width" in message:
        state["max_width"] = min(1920, max(640, int(message["max_width"])))
    print(
        f"[config] fps={state['fps']:g} quality={state['quality']} max_width={state['max_width']}",
        flush=True,
    )


async def receive_commands(websocket, state: dict) -> None:
    async for raw in websocket:
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            continue
        kind = message.get("type")
        if kind == "config":
            apply_config(state, message)
        elif kind == "ping":
            await websocket.send(json.dumps({"type": "pong"}))


async def send_frames(websocket, sct: MSS, monitor: dict, state: dict) -> None:
    while True:
        started = time.perf_counter()
        frame = build_frame(sct, monitor, state)
        await websocket.send(json.dumps(frame, ensure_ascii=False))
        interval = 1.0 / state["fps"]
        await asyncio.sleep(max(0.0, interval - (time.perf_counter() - started)))


async def run(args: argparse.Namespace) -> None:
    uri = build_uri(args)
    state = {
        "fps": min(30.0, max(1.0, args.fps)),
        "quality": min(95, max(20, args.quality)),
        "max_width": min(1920, max(640, args.max_width)),
        "seq": 0,
    }

    print("=" * 68)
    print("屏幕采集端已启动。")
    print("注意：本程序会持续截取本机屏幕并上传到中转服务。")
    print("请只在自己拥有或已获得明确授权的设备上运行。按 Ctrl+C 停止。")
    print(f"目标服务：{uri.split('?')[0]}")
    print(f"设备标识：{args.agent_id}")
    print("=" * 68, flush=True)

    reconnect_delay = 2.0
    try:
        while True:
            try:
                async with websockets.connect(
                    uri, open_timeout=10, ping_interval=20, ping_timeout=20
                ) as websocket:
                    with MSS() as sct:
                        monitors = sct.monitors
                        if args.monitor >= len(monitors):
                            print(f"[error] 显示器编号 {args.monitor} 不存在，共 {len(monitors) - 1} 个屏幕", flush=True)
                            return
                        monitor = monitors[args.monitor]
                        reconnect_delay = 2.0
                        print("[status] 已连接，开始推流", flush=True)

                        receiver = asyncio.create_task(receive_commands(websocket, state))
                        sender = asyncio.create_task(send_frames(websocket, sct, monitor, state))
                        done, pending = await asyncio.wait(
                            {receiver, sender}, return_when=asyncio.FIRST_COMPLETED
                        )
                        for task in pending:
                            task.cancel()
                        await asyncio.gather(*pending, return_exceptions=True)
                        for task in done:
                            error = task.exception()
                            if error:
                                print(f"[warn] 任务结束：{error}", flush=True)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"[warn] 连接失败：{exc}；{reconnect_delay:g} 秒后重试", flush=True)
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(30.0, reconnect_delay * 1.5)
    except KeyboardInterrupt:
        print("\n[status] 已停止采集", flush=True)


def main() -> None:
    args = parse_args()
    if args.list_monitors:
        list_monitors()
        return
    if not args.token:
        print("[error] 缺少 --token，请指定与服务端一致的令牌")
        return
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
