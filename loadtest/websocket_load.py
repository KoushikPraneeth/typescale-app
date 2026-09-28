#!/usr/bin/env python3
"""Measure TypeScale WebSocket connection and matchmaking behavior."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import websockets


@dataclass
class ClientResult:
    client_id: str
    connect_start: str
    connected_at: str | None = None
    join_sent_at: str | None = None
    room_joined_at: str | None = None
    race_started_at: str | None = None
    race_finished_at: str | None = None
    disconnected_at: str | None = None
    success: bool = False
    unexpected_disconnect: bool = False
    failure_reason: str | None = None
    join_latency_ms: float | None = None


@dataclass
class LoadState:
    active: int = 0
    peak: int = 0
    joined: int = 0
    failures: int = 0


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def elapsed_ms(start: float, end: float) -> float:
    return round((end - start) * 1000, 3)


async def hold_client(
    url: str,
    index: int,
    hold_seconds: float,
    ramp_delay: float,
    state: LoadState,
    lock: asyncio.Lock,
    complete_races: bool,
) -> ClientResult:
    await asyncio.sleep(index * ramp_delay)
    client_id = f"load-{index:04d}"
    result = ClientResult(client_id=client_id, connect_start=now_iso())
    paragraph = ""
    race_finished = asyncio.Event()
    try:
        async with websockets.connect(
            url,
            open_timeout=15,
            close_timeout=5,
            ping_interval=10,
            ping_timeout=10,
        ) as socket:
            result.connected_at = now_iso()
            join_started = time.perf_counter()
            result.join_sent_at = now_iso()
            await socket.send(json.dumps({"type": "join", "nickname": client_id}))

            while True:
                message = json.loads(await asyncio.wait_for(socket.recv(), timeout=20))
                message_type = message.get("type")
                if message_type == "joined":
                    result.room_joined_at = now_iso()
                    result.join_latency_ms = elapsed_ms(
                        join_started, time.perf_counter()
                    )
                    break
                if message_type == "race_ready":
                    paragraph = str(message.get("paragraph", ""))
                if message_type == "race_started":
                    result.race_started_at = now_iso()
                if message_type == "race_finished":
                    result.race_finished_at = now_iso()
                    race_finished.set()
                if message_type == "error":
                    raise RuntimeError(message.get("message", "join rejected"))

            result.success = True
            async with lock:
                state.active += 1
                state.joined += 1
                state.peak = max(state.peak, state.active)
                print(
                    f"joined={state.joined} active={state.active} "
                    f"client={client_id} join_ms={result.join_latency_ms}",
                    flush=True,
                )

            deadline = time.perf_counter() + hold_seconds
            while time.perf_counter() < deadline:
                remaining = max(0.1, deadline - time.perf_counter())
                try:
                    message = json.loads(
                        await asyncio.wait_for(socket.recv(), timeout=min(remaining, 15))
                    )
                    message_type = message.get("type")
                    if message_type == "race_ready":
                        paragraph = str(message.get("paragraph", ""))
                    elif message_type == "race_started" and result.race_started_at is None:
                        result.race_started_at = now_iso()
                        if complete_races and paragraph:
                            await socket.send(
                                json.dumps({"type": "progress", "text": paragraph})
                            )
                    elif message_type == "race_finished":
                        result.race_finished_at = now_iso()
                        race_finished.set()
                except asyncio.TimeoutError:
                    continue
        result.disconnected_at = now_iso()
    except Exception as exc:  # Preserve the concrete failure in the result artifact.
        result.unexpected_disconnect = result.success
        result.failure_reason = f"{type(exc).__name__}: {exc}".strip()
        result.disconnected_at = now_iso()
        async with lock:
            state.failures += 1
    finally:
        if result.success:
            async with lock:
                state.active -= 1
    return result


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, round((len(ordered) - 1) * fraction))
    return round(ordered[index], 3)


def summarize(results: list[ClientResult], state: LoadState) -> dict[str, Any]:
    successful = [result for result in results if result.success]
    latencies = [
        result.join_latency_ms
        for result in successful
        if result.join_latency_ms is not None
    ]
    completed = [result for result in successful if result.race_finished_at is not None]
    return {
        "attempted": len(results),
        "successful_connections": len(successful),
        "failed_connections": len(results) - len(successful),
        "unexpected_disconnects": sum(
            result.unexpected_disconnect for result in results
        ),
        "success_rate_percent": round((len(successful) / len(results) * 100), 3)
        if results
        else 0.0,
        "joined_players": len(successful),
        "completed_players": len(completed),
        "completion_rate_percent": round((len(completed) / len(successful) * 100), 3)
        if successful
        else 0.0,
        "peak_concurrent_websockets": state.peak,
        "join_latency_ms": {
            "count": len(latencies),
            "p50": percentile(latencies, 0.50),
            "p95": percentile(latencies, 0.95),
            "p99": percentile(latencies, 0.99),
        },
        "failures": [
            {"client_id": result.client_id, "reason": result.failure_reason}
            for result in results
            if not result.success or result.unexpected_disconnect
        ],
    }


def write_outputs(
    results: list[ClientResult],
    summary: dict[str, Any],
    json_path: Path | None,
    csv_path: Path | None,
) -> None:
    if json_path:
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(
            json.dumps(
                {"summary": summary, "clients": [asdict(result) for result in results]},
                indent=2,
            )
            + "\n"
        )
    if csv_path:
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        rows = [asdict(result) for result in results]
        fieldnames = list(asdict(ClientResult("", "")))
        with csv_path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)


async def run(args: argparse.Namespace) -> int:
    state = LoadState()
    lock = asyncio.Lock()
    results = await asyncio.gather(
        *(
            hold_client(
                args.url,
                index,
                args.hold_seconds,
                args.ramp_seconds / max(args.clients - 1, 1),
                state,
                lock,
                args.complete_races,
            )
            for index in range(args.clients)
        )
    )
    results = list(results)
    summary = summarize(results, state)
    write_outputs(
        results,
        summary,
        Path(args.json_out) if args.json_out else None,
        Path(args.csv_out) if args.csv_out else None,
    )
    print(json.dumps(summary, indent=2), flush=True)
    return 0 if summary["failed_connections"] == 0 else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True, help="WebSocket URL including /ws")
    parser.add_argument("--clients", type=int, default=40)
    parser.add_argument("--hold-seconds", type=float, default=120)
    parser.add_argument("--ramp-seconds", type=float, default=5)
    parser.add_argument("--json-out", help="Write per-client results and summary as JSON")
    parser.add_argument("--csv-out", help="Write one row per client as CSV")
    parser.add_argument(
        "--complete-races",
        action="store_true",
        help="Submit the full shared paragraph when each race starts",
    )
    args = parser.parse_args()
    if args.clients < 1:
        parser.error("--clients must be at least 1")
    if args.complete_races and args.clients < 2:
        parser.error("--complete-races requires at least two clients")
    if args.hold_seconds <= 0 or args.ramp_seconds < 0:
        parser.error("hold time must be positive and ramp time cannot be negative")
    return args


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(parse_args())))
