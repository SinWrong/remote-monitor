"""公网中转服务：把 Windows 采集端的屏幕画面实时转发给浏览器观看端。

启动示例：
    MONITOR_TOKEN=你的密钥 python -m uvicorn server:app --host 0.0.0.0 --port 8000

未设置 MONITOR_TOKEN 时，服务启动会自动生成一个随机令牌并打印在控制台。
画面只在内存中流转，不写入磁盘。
"""

import asyncio
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

TOKEN = os.environ.get("MONITOR_TOKEN") or secrets.token_urlsafe(24)
TOKEN_WAS_GENERATED = "MONITOR_TOKEN" not in os.environ

app = FastAPI(title="Remote Monitor Relay")
if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@dataclass
class AgentSession:
    websocket: WebSocket
    agent_id: str
    name: str
    viewers: set[WebSocket] = field(default_factory=set)
    latest_frame: dict[str, Any] | None = None
    frame_count: int = 0

    def public_info(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "online": True,
            "frame_count": self.frame_count,
        }


class Hub:
    def __init__(self) -> None:
        self.agents: dict[str, AgentSession] = {}
        self.lock = asyncio.Lock()

    async def register_agent(self, agent: AgentSession) -> None:
        async with self.lock:
            self.agents[agent.agent_id] = agent
        print(f"[agent] online: {agent.agent_id} ({agent.name})")

    async def remove_agent(self, agent: AgentSession) -> None:
        async with self.lock:
            if self.agents.get(agent.agent_id) is agent:
                self.agents.pop(agent.agent_id, None)
        print(f"[agent] offline: {agent.agent_id}")

    async def add_viewer(self, agent_id: str, ws: WebSocket) -> AgentSession | None:
        async with self.lock:
            agent = self.agents.get(agent_id)
            if agent is not None:
                agent.viewers.add(ws)
            return agent

    async def remove_viewer(self, agent_id: str, ws: WebSocket) -> None:
        async with self.lock:
            agent = self.agents.get(agent_id)
            if agent is not None:
                agent.viewers.discard(ws)

    async def list_agents(self) -> list[dict[str, Any]]:
        async with self.lock:
            return [agent.public_info() for agent in self.agents.values()]

    async def publish_frame(self, agent: AgentSession, frame: dict[str, Any]) -> None:
        agent.latest_frame = frame
        agent.frame_count += 1
        async with self.lock:
            targets = list(agent.viewers)
        dead: list[WebSocket] = []
        for viewer in targets:
            try:
                await viewer.send_json(frame)
            except Exception:
                dead.append(viewer)
        if dead:
            async with self.lock:
                for viewer in dead:
                    agent.viewers.discard(viewer)

    async def notify_agents_changed(self) -> None:
        message = {
            "type": "agent_list",
            "agents": await self.list_agents(),
        }
        async with self.lock:
            all_viewers = {
                viewer
                for agent in self.agents.values()
                for viewer in list(agent.viewers)
            }
        for viewer in all_viewers:
            try:
                await viewer.send_json(message)
            except Exception:
                pass


hub = Hub()


@app.on_event("startup")
async def on_startup() -> None:
    if TOKEN_WAS_GENERATED:
        print("=" * 64)
        print("未设置 MONITOR_TOKEN，本次自动生成令牌：")
        print(f"  {TOKEN}")
        print("重启后会变化；正式使用请设置环境变量固定令牌。")
        print("=" * 64)


@app.get("/")
async def index():
    index_file = STATIC_DIR / "index.html"
    if index_file.is_file():
        return FileResponse(index_file)
    return HTMLResponse("<h1>画面接收服务已启动，但找不到 static/index.html</h1>")


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "agents": await hub.list_agents()}


@app.websocket("/ws/agent")
async def agent_endpoint(
    websocket: WebSocket,
    token: str = "",
    agent_id: str = "default",
    name: str = "",
) -> None:
    if token != TOKEN:
        await websocket.close(code=4401, reason="invalid token")
        return
    await websocket.accept()
    agent = AgentSession(
        websocket=websocket,
        agent_id=agent_id,
        name=name or agent_id,
    )
    await hub.register_agent(agent)
    await hub.notify_agents_changed()
    try:
        while True:
            message = await websocket.receive_json()
            if message.get("type") == "frame" and isinstance(message.get("data"), str):
                await hub.publish_frame(agent, message)
            elif message.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        print(f"[agent] connection error: {exc}")
    finally:
        await hub.remove_agent(agent)
        await hub.notify_agents_changed()


@app.websocket("/ws/viewer")
async def viewer_endpoint(
    websocket: WebSocket,
    token: str = "",
    agent_id: str = "default",
) -> None:
    if token != TOKEN:
        await websocket.close(code=4401, reason="invalid token")
        return
    await websocket.accept()
    await hub.add_viewer(agent_id, websocket)
    try:
        await websocket.send_json(
            {
                "type": "agent_list",
                "agents": await hub.list_agents(),
            }
        )
        async with hub.lock:
            agent = hub.agents.get(agent_id)
            snapshot = agent.latest_frame if agent is not None else None
        if snapshot is not None:
            await websocket.send_json(snapshot)

        while True:
            message = await websocket.receive_json()
            if message.get("type") == "config":
                async with hub.lock:
                    target = hub.agents.get(agent_id)
                if target is None:
                    await websocket.send_json(
                        {"type": "notice", "text": "采集端当前不在线，配置未发送"}
                    )
                else:
                    await target.websocket.send_json(message)
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        print(f"[viewer] connection error: {exc}")
    finally:
        await hub.remove_viewer(agent_id, websocket)
