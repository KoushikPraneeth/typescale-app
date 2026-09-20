import asyncio
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "frontend"
PARAGRAPHS = [
    "Small reliable systems are built one clear decision at a time.",
    "Cloud platforms become useful when teams can deploy safely and recover quickly.",
    "Measure what matters, automate repeatable work, and learn from every failure.",
    "A calm engineer reads the evidence before changing a production system.",
]
NICKNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _-]{1,19}$")

app = FastAPI(title="TypeScale", version="0.1.0")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@dataclass
class Player:
    player_id: str
    nickname: str
    socket: WebSocket
    typed_chars: int = 0
    correct_chars: int = 0
    accuracy: float = 100.0
    wpm: float = 0.0
    placement: Optional[int] = None
    connected: bool = True


@dataclass
class Room:
    room_id: str
    paragraph: str
    players: Dict[str, Player] = field(default_factory=dict)
    state: str = "waiting"
    started_at: Optional[float] = None
    countdown_task: Optional[asyncio.Task] = None


class GameManager:
    def __init__(self) -> None:
        self.rooms: Dict[str, Room] = {}
        self.lock = asyncio.Lock()
        self.paragraph_index = 0

    async def _send(self, player: Player, payload: dict) -> None:
        if not player.connected:
            return
        try:
            await player.socket.send_json(payload)
        except Exception:
            player.connected = False

    async def broadcast(self, room: Room, payload: dict) -> None:
        await asyncio.gather(
            *(self._send(player, payload) for player in list(room.players.values())),
            return_exceptions=True,
        )

    def roster(self, room: Room) -> List[dict]:
        return [
            {
                "playerId": p.player_id,
                "nickname": p.nickname,
                "progress": round(100 * p.typed_chars / len(room.paragraph), 1),
                "correctChars": p.correct_chars,
                "wpm": round(p.wpm, 1),
                "accuracy": round(p.accuracy, 1),
                "placement": p.placement,
                "connected": p.connected,
            }
            for p in room.players.values()
        ]

    async def join(self, socket: WebSocket, nickname: str) -> tuple:
        async with self.lock:
            room = next(
                (
                    candidate
                    for candidate in self.rooms.values()
                    if candidate.state in {"waiting", "countdown"}
                    and len(candidate.players) < 4
                ),
                None,
            )
            if room is None:
                room = Room(
                    room_id=str(uuid.uuid4()),
                    paragraph=PARAGRAPHS[self.paragraph_index % len(PARAGRAPHS)],
                )
                self.paragraph_index += 1
                self.rooms[room.room_id] = room

            player = Player(str(uuid.uuid4()), nickname, socket)
            room.players[player.player_id] = player
            if len(room.players) >= 2 and room.countdown_task is None:
                room.state = "countdown"
                room.countdown_task = asyncio.create_task(self.run_countdown(room))

        await self._send(
            player,
            {
                "type": "joined",
                "roomId": room.room_id,
                "playerId": player.player_id,
                "maxPlayers": 4,
            },
        )
        await self.broadcast(
            room,
            {"type": "lobby", "roomId": room.room_id, "players": self.roster(room)},
        )
        return room, player

    async def run_countdown(self, room: Room) -> None:
        await self.broadcast(
            room,
            {
                "type": "race_ready",
                "roomId": room.room_id,
                "paragraph": room.paragraph,
                "players": self.roster(room),
            },
        )
        for value in (3, 2, 1):
            await self.broadcast(room, {"type": "countdown", "value": value})
            await asyncio.sleep(1)
        async with self.lock:
            if room.state != "countdown":
                return
            room.state = "racing"
            room.started_at = time.monotonic()
        await self.broadcast(
            room,
            {
                "type": "race_started",
                "startedAt": int(time.time() * 1000),
                "players": self.roster(room),
            },
        )

    async def progress(self, room: Room, player: Player, typed: str) -> None:
        if room.state != "racing" or room.started_at is None:
            await self._send(player, {"type": "error", "message": "The race has not started."})
            return
        if not isinstance(typed, str) or len(typed) > len(room.paragraph) + 50:
            await self._send(player, {"type": "error", "message": "Invalid typing update."})
            return

        expected = room.paragraph
        incorrect_positions = [
            index
            for index, actual in enumerate(typed)
            if index >= len(expected) or actual != expected[index]
        ]
        player.typed_chars = min(len(typed), len(expected))
        player.correct_chars = sum(
            actual == wanted for actual, wanted in zip(typed, expected)
        )
        player.accuracy = (
            100.0 if not typed else 100.0 * player.correct_chars / len(typed)
        )
        elapsed = max(time.monotonic() - room.started_at, 0.25)
        player.wpm = (player.correct_chars / 5.0) / (elapsed / 60.0)

        if incorrect_positions:
            await self._send(
                player,
                {
                    "type": "validation_error",
                    "typedChars": player.typed_chars,
                    "correctChars": player.correct_chars,
                    "incorrectPositions": incorrect_positions,
                    "accuracy": round(player.accuracy, 1),
                },
            )

        if len(typed) >= len(expected) and player.placement is None:
            player.placement = 1 + sum(
                1 for candidate in room.players.values() if candidate.placement is not None
            )

        await self.broadcast(
            room,
            {
                "type": "player_progress",
                "player": self.roster(room)[list(room.players).index(player.player_id)],
                "players": self.roster(room),
            },
        )

        connected = [p for p in room.players.values() if p.connected]
        if connected and all(p.placement is not None for p in connected):
            room.state = "finished"
            standings = sorted(connected, key=lambda p: p.placement or 999)
            await self.broadcast(
                room,
                {
                    "type": "race_finished",
                    "standings": [
                        {
                            "placement": p.placement,
                            "playerId": p.player_id,
                            "nickname": p.nickname,
                            "wpm": round(p.wpm, 1),
                            "accuracy": round(p.accuracy, 1),
                        }
                        for p in standings
                    ],
                },
            )

    async def disconnect(self, room: Optional[Room], player: Optional[Player]) -> None:
        if room is None or player is None:
            return
        player.connected = False
        if room.state in {"waiting", "countdown"}:
            room.players.pop(player.player_id, None)
            if room.state == "countdown" and len(room.players) < 2:
                if room.countdown_task:
                    room.countdown_task.cancel()
                room.countdown_task = None
                room.state = "waiting"
        if not room.players or not any(p.connected for p in room.players.values()):
            self.rooms.pop(room.room_id, None)
            return
        await self.broadcast(
            room,
            {"type": "player_left", "playerId": player.player_id, "players": self.roster(room)},
        )


manager = GameManager()


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.websocket("/ws")
async def websocket_endpoint(socket: WebSocket) -> None:
    await socket.accept()
    room: Optional[Room] = None
    player: Optional[Player] = None
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
        room, player = await manager.join(socket, nickname)
        while True:
            message = json.loads(await socket.receive_text())
            if message.get("type") == "progress":
                await manager.progress(room, player, message.get("text", ""))
            elif message.get("type") == "ping":
                await socket.send_json({"type": "pong"})
    except (WebSocketDisconnect, asyncio.TimeoutError, json.JSONDecodeError):
        pass
    finally:
        await manager.disconnect(room, player)
