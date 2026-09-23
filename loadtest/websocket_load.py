#!/usr/bin/env python3
"""Hold concurrent TypeScale WebSockets open to exercise KEDA scaling."""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass

import websockets


@dataclass
class LoadState:
    active: int = 0
    peak: int = 0
    joined: int = 0


async def hold_client(
    url: str,
    index: int,
    hold_seconds: float,
    ramp_delay: float,
    state: LoadState,
    lock: asyncio.Lock,
) -> None:
    await asyncio.sleep(index * ramp_delay)
    async with websockets.connect(url, open_timeout=10, close_timeout=5) as socket:
        await socket.send(
            json.dumps({"type": "join", "nickname": f"Load-{index:04d}"})
        )
        while True:
            message = json.loads(await asyncio.wait_for(socket.recv(), timeout=15))
            if message.get("type") == "joined":
                break
            if message.get("type") == "error":
                raise RuntimeError(message.get("message", "join rejected"))

        async with lock:
            state.active += 1
            state.joined += 1
            state.peak = max(state.peak, state.active)
            if state.joined == 1 or state.joined % 10 == 0:
                print(f"joined={state.joined} active={state.active}", flush=True)

        try:
            await asyncio.sleep(hold_seconds)
        finally:
            async with lock:
                state.active -= 1


async def run(args: argparse.Namespace) -> None:
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
            )
            for index in range(args.clients)
        ),
        return_exceptions=True,
    )
    failures = [result for result in results if isinstance(result, BaseException)]
    print(
        f"completed={args.clients - len(failures)} failures={len(failures)} "
        f"peak_connections={state.peak}",
        flush=True,
    )
    if failures:
        for failure in failures[:5]:
            print(f"failure: {failure!r}", flush=True)
        raise SystemExit(1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True, help="TypeScale WebSocket URL, including /ws")
    parser.add_argument("--clients", type=int, default=40)
    parser.add_argument("--hold-seconds", type=float, default=120)
    parser.add_argument("--ramp-seconds", type=float, default=5)
    args = parser.parse_args()
    if args.clients < 1:
        parser.error("--clients must be at least 1")
    if args.hold_seconds <= 0 or args.ramp_seconds < 0:
        parser.error("hold time must be positive and ramp time cannot be negative")
    return args


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
