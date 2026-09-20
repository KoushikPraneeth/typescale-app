import asyncio
import contextlib
import json
import math
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, Optional

from fastapi import WebSocket

from app.game_logic import calculate_progress
from app.redis_repository import RedisRoomRepository

PARAGRAPHS = [
    "Small reliable systems are built one clear decision at a time.",
    "Cloud platforms become useful when teams can deploy safely and recover quickly.",
    "Measure what matters, automate repeatable work, and learn from every failure.",
    "A calm engineer reads the evidence before changing a production system.",
]


@dataclass
class LocalConnection:
    socket: WebSocket
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class DistributedGameManager:
    def __init__(self, repository: RedisRoomRepository) -> None:
        self.repository = repository
        self.connections: Dict[str, Dict[str, LocalConnection]] = {}
        self.pubsub = None
        self.listener_task: Optional[asyncio.Task] = None
        self.countdown_tasks: Dict[str, asyncio.Task] = {}

    async def start(self) -> None:
        await self.repository.ping()
        self.pubsub = self.repository.redis.pubsub()
        await self.pubsub.psubscribe(self.repository.event_pattern)
        self.listener_task = asyncio.create_task(self._listen_for_events())

    async def close(self) -> None:
        for task in list(self.countdown_tasks.values()):
            task.cancel()
        if self.listener_task:
            self.listener_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.listener_task
        if self.pubsub:
            await self.pubsub.aclose()
        await self.repository.close()

    async def _listen_for_events(self) -> None:
        assert self.pubsub is not None
        while True:
            message = await self.pubsub.get_message(
                ignore_subscribe_messages=True, timeout=1.0
            )
            if message and message.get("type") == "pmessage":
                try:
                    event = json.loads(message["data"])
                    await self._send_to_local_room(event["roomId"], event)
                except (KeyError, TypeError, json.JSONDecodeError):
                    pass
            await asyncio.sleep(0)

    async def _send(self, connection: LocalConnection, payload: Dict) -> bool:
        try:
            async with connection.lock:
                await connection.socket.send_json(payload)
            return True
        except Exception:
            return False

    async def _send_to_player(self, room_id: str, player_id: str, payload: Dict) -> None:
        connection = self.connections.get(room_id, {}).get(player_id)
        if connection and not await self._send(connection, payload):
            self._unregister(room_id, player_id)

    async def _send_to_local_room(self, room_id: str, payload: Dict) -> None:
        local = list(self.connections.get(room_id, {}).items())
        results = await asyncio.gather(
            *(self._send(connection, payload) for _, connection in local),
            return_exceptions=True,
        )
        for (player_id, _), result in zip(local, results):
            if result is not True:
                self._unregister(room_id, player_id)

    def _register(self, room_id: str, player_id: str, socket: WebSocket) -> None:
        self.connections.setdefault(room_id, {})[player_id] = LocalConnection(socket)

    def _unregister(self, room_id: str, player_id: str) -> None:
        room_connections = self.connections.get(room_id)
        if not room_connections:
            return
        room_connections.pop(player_id, None)
        if not room_connections:
            self.connections.pop(room_id, None)

    @staticmethod
    def _roster(snapshot: Dict) -> list:
        paragraph_length = max(len(snapshot["paragraph"]), 1)
        players = sorted(
            snapshot["players"],
            key=lambda player: (player.get("joinedAt", 0), player["playerId"]),
        )
        return [
            {
                "playerId": player["playerId"],
                "nickname": player["nickname"],
                "progress": round(
                    100 * player.get("typedChars", 0) / paragraph_length, 1
                ),
                "correctChars": player.get("correctChars", 0),
                "wpm": round(player.get("wpm", 0.0), 1),
                "accuracy": round(player.get("accuracy", 100.0), 1),
                "placement": player.get("placement"),
                "connected": player.get("connected", False),
            }
            for player in players
        ]

    @staticmethod
    def _standings(snapshot: Dict) -> list:
        players = [
            player
            for player in snapshot["players"]
            if player.get("placement") is not None
        ]
        players.sort(key=lambda player: (player["placement"], player["playerId"]))
        return [
            {
                "placement": player["placement"],
                "playerId": player["playerId"],
                "nickname": player["nickname"],
                "wpm": round(player.get("wpm", 0.0), 1),
                "accuracy": round(player.get("accuracy", 100.0), 1),
            }
            for player in players
        ]

    async def join(self, socket: WebSocket, nickname: str) -> tuple:
        player_id = str(uuid.uuid4())
        paragraph_index = int(await self.repository.redis.incr(
            f"{self.repository.namespace}:paragraph:index"
        )) - 1
        player = {
            "playerId": player_id,
            "nickname": nickname,
            "typedChars": 0,
            "correctChars": 0,
            "accuracy": 100.0,
            "wpm": 0.0,
            "placement": None,
            "connected": True,
            "joinedAt": int(time.time() * 1000),
        }
        snapshot, should_start = await self.repository.join(
            player, PARAGRAPHS[paragraph_index % len(PARAGRAPHS)]
        )
        room_id = snapshot["roomId"]
        self._register(room_id, player_id, socket)
        await self._send_to_player(
            room_id,
            player_id,
            {
                "type": "joined",
                "roomId": room_id,
                "playerId": player_id,
                "maxPlayers": 4,
            },
        )
        await self.repository.publish(
            room_id,
            {
                "type": "lobby",
                "players": self._roster(snapshot),
            },
        )

        if should_start:
            task = asyncio.create_task(self._run_countdown(room_id))
            self.countdown_tasks[room_id] = task
            task.add_done_callback(lambda _: self.countdown_tasks.pop(room_id, None))
        elif snapshot["state"] == "countdown":
            await self._send_to_player(
                room_id,
                player_id,
                {
                    "type": "race_ready",
                    "roomId": room_id,
                    "paragraph": snapshot["paragraph"],
                    "players": self._roster(snapshot),
                },
            )
            remaining = max(
                1,
                math.ceil(
                    (snapshot["countdownDeadline"] - time.time() * 1000) / 1000
                ),
            )
            await self._send_to_player(
                room_id, player_id, {"type": "countdown", "value": remaining}
            )
        return room_id, player_id

    async def _run_countdown(self, room_id: str) -> None:
        snapshot = await self.repository.snapshot(room_id)
        await self.repository.publish(
            room_id,
            {
                "type": "race_ready",
                "paragraph": snapshot["paragraph"],
                "players": self._roster(snapshot),
            },
        )
        deadline = snapshot["countdownDeadline"]
        for value in (3, 2, 1):
            room = await self.repository.room(room_id)
            if not room or room["state"] != "countdown":
                return
            await self.repository.publish(room_id, {"type": "countdown", "value": value})
            next_tick = deadline - (value - 1) * 1000
            await asyncio.sleep(max(0, (next_tick - time.time() * 1000) / 1000))
        started_at = int(time.time() * 1000)
        if await self.repository.start_race(room_id, started_at):
            snapshot = await self.repository.snapshot(room_id)
            await self.repository.publish(
                room_id,
                {
                    "type": "race_started",
                    "startedAt": started_at,
                    "players": self._roster(snapshot),
                },
            )

    async def progress(
        self, room_id: str, player_id: str, typed: object
    ) -> None:
        room = await self.repository.room(room_id)
        if room is None or room["state"] != "racing":
            await self._send_to_player(
                room_id, player_id, {"type": "error", "message": "The race has not started."}
            )
            return
        if not isinstance(typed, str) or len(typed) > len(room["paragraph"]) + 50:
            await self._send_to_player(
                room_id, player_id, {"type": "error", "message": "Invalid typing update."}
            )
            return
        players = await self.repository.players(room_id)
        player = next(
            (candidate for candidate in players if candidate["playerId"] == player_id),
            None,
        )
        if player is None:
            return
        metrics = calculate_progress(room["paragraph"], typed, room["startedAt"])
        player.update(
            {
                "typedChars": metrics.typed_chars,
                "correctChars": metrics.correct_chars,
                "accuracy": metrics.accuracy,
                "wpm": metrics.wpm,
                "connected": True,
            }
        )
        try:
            snapshot, finalized = await self.repository.update_progress(
                room_id, player_id, player, len(room["paragraph"])
            )
        except RuntimeError:
            return
        if metrics.incorrect_positions:
            await self._send_to_player(
                room_id,
                player_id,
                {
                    "type": "validation_error",
                    "typedChars": metrics.typed_chars,
                    "correctChars": metrics.correct_chars,
                    "incorrectPositions": metrics.incorrect_positions,
                    "accuracy": round(metrics.accuracy, 1),
                },
            )
        await self.repository.publish(
            room_id,
            {
                "type": "player_progress",
                "player": next(
                    item
                    for item in self._roster(snapshot)
                    if item["playerId"] == player_id
                ),
                "players": self._roster(snapshot),
            },
        )
        if finalized:
            await self.repository.publish(
                room_id,
                {
                    "type": "race_finished",
                    "standings": self._standings(snapshot),
                },
            )

    async def disconnect(self, room_id: Optional[str], player_id: Optional[str]) -> None:
        if room_id is None or player_id is None:
            return
        self._unregister(room_id, player_id)
        snapshot, finalized = await self.repository.disconnect(room_id, player_id)
        if snapshot is None:
            return
        await self.repository.publish(
            room_id,
            {
                "type": "player_left",
                "playerId": player_id,
                "players": self._roster(snapshot),
            },
        )
        if finalized:
            await self.repository.publish(
                room_id,
                {
                    "type": "race_finished",
                    "standings": self._standings(snapshot),
                },
            )
