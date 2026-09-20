import asyncio
from collections import Counter
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
import websockets
from fastapi.testclient import TestClient
from redis import Redis

from app.main import app

ROOT = Path(__file__).resolve().parents[1]
TEST_REDIS_URL = "redis://127.0.0.1:6379/15"


@pytest.fixture(autouse=True)
def isolated_redis(monkeypatch):
    namespace = f"typescale:test:{uuid.uuid4().hex}"
    monkeypatch.setenv("REDIS_URL", TEST_REDIS_URL)
    monkeypatch.setenv("TYPESCALE_NAMESPACE", namespace)
    client: Any = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    assert client.ping()
    client.flushdb()
    yield
    client.flushdb()
    client.close()


def test_health_and_frontend():
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        page = client.get("/")
        assert page.status_code == 200
        assert "TypeScale" in page.text


def test_passage_highlights_only_the_incorrect_character():
    script = r'''
const fs = require("fs");
const vm = require("vm");
const elements = new Proxy({}, {
  get(target, key) {
    if (!target[key]) target[key] = {
      innerHTML: "", textContent: "", value: "", disabled: false,
      classList: {toggle() {}, add() {}, remove() {}, contains() { return false; }},
      addEventListener() {}, focus() {}
    };
    return target[key];
  }
});
global.document = {getElementById: id => elements[id]};
global.location = {protocol: "http:", host: "localhost"};
vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8"));
paragraph = "abcd";
renderPassage("axc");
process.stdout.write(elements.passage.innerHTML);
'''
    result = subprocess.run(
        ["node", "-e", script, str(ROOT / "frontend" / "app.js")],
        capture_output=True,
        check=True,
        text=True,
    )
    assert result.stdout == (
        '<span class="done">a</span>'
        '<span class="wrong">b</span>'
        '<span class="done">c</span>'
        '<span class="next">d</span>'
    )


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def start_server(port):
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=ROOT,
        env=os.environ.copy(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            response = httpx.get(
                f"http://127.0.0.1:{port}/health", timeout=0.25
            )
            if response.status_code == 200:
                return process
        except httpx.HTTPError:
            time.sleep(0.05)
    process.terminate()
    process.wait(timeout=5)
    raise RuntimeError(f"Server on port {port} did not become ready")


def stop_server(process):
    process.terminate()
    process.wait(timeout=5)


@pytest.fixture
def live_server():
    port = free_port()
    process = start_server(port)
    yield port
    stop_server(process)


@pytest.fixture
def two_live_servers():
    ports = (free_port(), free_port())
    processes = [start_server(port) for port in ports]
    yield ports
    for process in processes:
        stop_server(process)


async def receive_until(ws, event_type, timeout=8):
    async def wait():
        while True:
            message = json.loads(await ws.recv())
            if message.get("type") == event_type:
                return message

    return await asyncio.wait_for(wait(), timeout=timeout)


async def play_two_player_race(port_a, port_b):
    uri_a = f"ws://127.0.0.1:{port_a}/ws"
    uri_b = f"ws://127.0.0.1:{port_b}/ws"
    async with websockets.connect(uri_a) as alpha, websockets.connect(uri_b) as beta:
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

        one_wrong = paragraph[0] + "x" + paragraph[2:5]
        await alpha.send(json.dumps({"type": "progress", "text": one_wrong}))
        rejected = await receive_until(alpha, "validation_error")
        assert rejected["incorrectPositions"] == [1]
        assert rejected["correctChars"] == 4
        assert rejected["accuracy"] == 80.0
        progress_a, progress_b = await asyncio.gather(
            receive_until(alpha, "player_progress"),
            receive_until(beta, "player_progress"),
        )
        assert progress_a["player"] == progress_b["player"]
        alpha_state = next(
            player
            for player in progress_a["players"]
            if player["nickname"] == "Alpha"
        )
        assert alpha_state["progress"] == round(500 / len(paragraph), 1)

        alpha_finish = paragraph[0] + "x" + paragraph[2:]
        await alpha.send(json.dumps({"type": "progress", "text": alpha_finish}))
        await asyncio.gather(
            receive_until(alpha, "player_progress"),
            receive_until(beta, "player_progress"),
        )
        await beta.send(json.dumps({"type": "progress", "text": paragraph}))
        results_a, results_b = await asyncio.gather(
            receive_until(alpha, "race_finished"),
            receive_until(beta, "race_finished"),
        )
        assert results_a == results_b
        assert [item["nickname"] for item in results_a["standings"]] == [
            "Alpha",
            "Beta",
        ]
        assert all(item["wpm"] > 0 for item in results_a["standings"])
        assert results_a["standings"][0]["accuracy"] < 100
        return joined_a["roomId"]


def test_real_two_client_websocket_race(live_server):
    asyncio.run(play_two_player_race(live_server, live_server))


def test_cross_process_websocket_race(two_live_servers):
    room_id = asyncio.run(play_two_player_race(*two_live_servers))
    client: Any = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    room_key = f"{os.environ['TYPESCALE_NAMESPACE']}:room:{room_id}"
    assert 0 < client.ttl(room_key) <= 900
    client.close()


async def join_many_players(ports, total):
    connections = []
    try:
        for index in range(total):
            port = ports[index % len(ports)]
            connection = await websockets.connect(f"ws://127.0.0.1:{port}/ws")
            connections.append(connection)
        await asyncio.gather(
            *(
                connection.send(
                    json.dumps({"type": "join", "nickname": f"Player-{index}"})
                )
                for index, connection in enumerate(connections)
            )
        )
        joined = await asyncio.gather(
            *(receive_until(connection, "joined") for connection in connections)
        )
        return Counter(message["roomId"] for message in joined)
    finally:
        await asyncio.gather(*(connection.close() for connection in connections))


def test_atomic_matchmaking_caps_rooms_at_four(two_live_servers):
    room_counts = asyncio.run(join_many_players(two_live_servers, 8))
    assert sum(room_counts.values()) == 8
    assert max(room_counts.values()) <= 4
