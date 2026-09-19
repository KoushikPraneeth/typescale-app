import asyncio
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
import websockets
from fastapi.testclient import TestClient

from app.main import app

ROOT = Path(__file__).resolve().parents[1]


def test_health_and_frontend():
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        page = client.get("/")
        assert page.status_code == 200
        assert "TypeScale" in page.text


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def live_server():
    port = free_port()
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            if httpx.get(f"http://127.0.0.1:{port}/health", timeout=.25).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(.05)
    else:
        process.terminate()
        raise RuntimeError("Server did not become ready")
    yield port
    process.terminate()
    process.wait(timeout=5)


async def receive_until(ws, event_type, timeout=8):
    async def wait():
        while True:
            message = json.loads(await ws.recv())
            if message.get("type") == event_type:
                return message
    return await asyncio.wait_for(wait(), timeout=timeout)


async def play_two_player_race(port):
    uri = f"ws://127.0.0.1:{port}/ws"
    async with websockets.connect(uri) as alpha, websockets.connect(uri) as beta:
        await alpha.send(json.dumps({"type": "join", "nickname": "Alpha"}))
        joined_a = await receive_until(alpha, "joined")
        await beta.send(json.dumps({"type": "join", "nickname": "Beta"}))
        joined_b = await receive_until(beta, "joined")
        assert joined_a["roomId"] == joined_b["roomId"]

        ready_a, ready_b = await asyncio.gather(
            receive_until(alpha, "race_ready"), receive_until(beta, "race_ready")
        )
        assert ready_a["paragraph"] == ready_b["paragraph"]
        paragraph = ready_a["paragraph"]
        await asyncio.gather(
            receive_until(alpha, "race_started"), receive_until(beta, "race_started")
        )

        await alpha.send(json.dumps({"type": "progress", "text": "wrong"}))
        rejected = await receive_until(alpha, "validation_error")
        assert rejected["validatedChars"] == 0
        await alpha.send(json.dumps({"type": "progress", "text": paragraph}))
        await receive_until(alpha, "player_progress")
        await beta.send(json.dumps({"type": "progress", "text": paragraph}))
        results_a, results_b = await asyncio.gather(
            receive_until(alpha, "race_finished"), receive_until(beta, "race_finished")
        )
        assert results_a == results_b
        assert [item["nickname"] for item in results_a["standings"]] == ["Alpha", "Beta"]
        assert all(item["wpm"] > 0 for item in results_a["standings"])


def test_real_two_client_websocket_race(live_server):
    asyncio.run(play_two_player_race(live_server))
