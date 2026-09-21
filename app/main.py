import asyncio
import json
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from redis.exceptions import RedisError

from app.game_manager import DistributedGameManager
from app.redis_repository import RedisRoomRepository

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "frontend"
NICKNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _-]{1,19}$")


@asynccontextmanager
async def lifespan(application: FastAPI):
    application.state.initialized = False
    repository = RedisRoomRepository(
        redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
        namespace=os.getenv("TYPESCALE_NAMESPACE", "typescale"),
    )
    manager = DistributedGameManager(repository)
    await manager.start()
    application.state.manager = manager
    application.state.initialized = True
    try:
        yield
    finally:
        application.state.initialized = False
        await manager.close()


app = FastAPI(title="TypeScale", version="0.2.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
async def health() -> dict:
    return await readiness()


@app.get("/health/live")
async def liveness() -> dict:
    return {"status": "ok"}


@app.get("/health/ready")
async def readiness() -> dict:
    try:
        await app.state.manager.repository.ping()
    except RedisError as error:
        raise HTTPException(status_code=503, detail="Redis is unavailable") from error
    return {"status": "ok"}


@app.get("/health/startup")
async def startup() -> dict:
    if not getattr(app.state, "initialized", False):
        raise HTTPException(status_code=503, detail="Application is not initialized")
    return {"status": "ok"}


@app.websocket("/ws")
async def websocket_endpoint(socket: WebSocket) -> None:
    await socket.accept()
    room_id: Optional[str] = None
    player_id: Optional[str] = None
    manager: DistributedGameManager = app.state.manager
    try:
        raw = await asyncio.wait_for(socket.receive_text(), timeout=15)
        message = json.loads(raw)
        nickname = str(message.get("nickname", "")).strip()
        if message.get("type") != "join" or not NICKNAME_RE.fullmatch(nickname):
            await socket.send_json(
                {
                    "type": "error",
                    "message": "Nickname must be 2-20 characters using letters, numbers, spaces, _ or -.",
                }
            )
            await socket.close(code=1008)
            return
        room_id, player_id = await manager.join(socket, nickname)
        assert room_id is not None and player_id is not None
        while True:
            message = json.loads(await socket.receive_text())
            if message.get("type") == "progress":
                await manager.progress(room_id, player_id, message.get("text", ""))
            elif message.get("type") == "ping":
                await socket.send_json({"type": "pong"})
    except (WebSocketDisconnect, asyncio.TimeoutError, json.JSONDecodeError):
        pass
    finally:
        await manager.disconnect(room_id, player_id)
