import asyncio
import json
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from time import perf_counter
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from prometheus_client import start_http_server
from fastapi.staticfiles import StaticFiles
from redis.exceptions import RedisError

from app.game_manager import DistributedGameManager
from app.metrics import (
    HTTP_REQUEST_DURATION,
    HTTP_REQUESTS,
    REDIS_ERRORS,
    WEBSOCKET_CONNECTIONS,
    WEBSOCKET_MESSAGES,
)
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
    metrics_server = None
    metrics_thread = None
    try:
        await manager.start()
        metrics_port = os.getenv("METRICS_PORT")
        if metrics_port:
            metrics_server, metrics_thread = start_http_server(int(metrics_port))
        application.state.manager = manager
        application.state.initialized = True
        yield
    finally:
        application.state.initialized = False
        try:
            await manager.close()
        finally:
            if metrics_server:
                await asyncio.to_thread(metrics_server.shutdown)
                await asyncio.to_thread(metrics_server.server_close)
            if metrics_thread:
                await asyncio.to_thread(metrics_thread.join, 5)


app = FastAPI(title="TypeScale", version="0.8.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.middleware("http")
async def observe_http(request: Request, call_next):
    started = perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        route = request.scope.get("route")
        route_path = getattr(route, "path", "unmatched")
        if route_path != "/metrics":
            HTTP_REQUESTS.labels(
                method=request.method,
                route=route_path,
                status=str(status_code),
            ).inc()
            HTTP_REQUEST_DURATION.labels(
                method=request.method,
                route=route_path,
            ).observe(perf_counter() - started)


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
        REDIS_ERRORS.labels(operation="readiness").inc()
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
    WEBSOCKET_CONNECTIONS.inc()
    room_id: Optional[str] = None
    player_id: Optional[str] = None
    manager: DistributedGameManager = app.state.manager
    try:
        raw = await asyncio.wait_for(socket.receive_text(), timeout=15)
        message = json.loads(raw)
        nickname = str(message.get("nickname", "")).strip()
        if message.get("type") != "join" or not NICKNAME_RE.fullmatch(nickname):
            WEBSOCKET_MESSAGES.labels(type="invalid").inc()
            await socket.send_json(
                {
                    "type": "error",
                    "message": "Nickname must be 2-20 characters using letters, numbers, spaces, _ or -.",
                }
            )
            await socket.close(code=1008)
            return
        WEBSOCKET_MESSAGES.labels(type="join").inc()
        room_id, player_id = await manager.join(socket, nickname)
        assert room_id is not None and player_id is not None
        while True:
            message = json.loads(await socket.receive_text())
            message_type = message.get("type")
            if message_type == "progress":
                WEBSOCKET_MESSAGES.labels(type="progress").inc()
                await manager.progress(room_id, player_id, message.get("text", ""))
            elif message_type == "ping":
                WEBSOCKET_MESSAGES.labels(type="ping").inc()
                await socket.send_json({"type": "pong"})
            else:
                WEBSOCKET_MESSAGES.labels(type="other").inc()
    except (WebSocketDisconnect, asyncio.TimeoutError, json.JSONDecodeError):
        pass
    except RedisError:
        REDIS_ERRORS.labels(operation="gameplay").inc()
    finally:
        WEBSOCKET_CONNECTIONS.dec()
        try:
            await manager.disconnect(room_id, player_id)
        except RedisError:
            REDIS_ERRORS.labels(operation="disconnect").inc()
