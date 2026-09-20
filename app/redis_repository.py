import json
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from redis.asyncio import Redis


JOIN_SCRIPT = r"""
local waiting_key = KEYS[1]
local room_prefix = ARGV[1]
local new_room_id = ARGV[2]
local paragraph = ARGV[3]
local player_id = ARGV[4]
local player_json = ARGV[5]
local now_ms = tonumber(ARGV[6])
local active_ttl = tonumber(ARGV[7])
local room_id = redis.call('GET', waiting_key)
local room_key = nil
local state = nil
local count = 0

if room_id then
  room_key = room_prefix .. room_id
  state = redis.call('HGET', room_key, 'state')
  count = tonumber(redis.call('HGET', room_key, 'playerCount') or '0')
  local deadline = tonumber(redis.call('HGET', room_key, 'countdownDeadline') or '0')
  if not state or count >= 4 or (state ~= 'waiting' and state ~= 'countdown') or
     (state == 'countdown' and deadline > 0 and deadline <= now_ms) then
    room_id = nil
  end
end

if not room_id then
  room_id = new_room_id
  room_key = room_prefix .. room_id
  state = 'waiting'
  count = 0
  redis.call('HSET', room_key,
    'roomId', room_id,
    'paragraph', paragraph,
    'state', state,
    'playerCount', 0,
    'finishCounter', 0,
    'createdAt', now_ms,
    'countdownDeadline', 0,
    'startedAt', 0)
  redis.call('SET', waiting_key, room_id, 'EX', active_ttl)
end

local players_key = room_key .. ':players'
redis.call('HSET', players_key, player_id, player_json)
count = redis.call('HINCRBY', room_key, 'playerCount', 1)
local should_start = 0
local deadline = tonumber(redis.call('HGET', room_key, 'countdownDeadline') or '0')
state = redis.call('HGET', room_key, 'state')
if count == 2 and state == 'waiting' then
  state = 'countdown'
  deadline = now_ms + 3000
  redis.call('HSET', room_key, 'state', state, 'countdownDeadline', deadline)
  should_start = 1
end
if count >= 4 then
  redis.call('DEL', waiting_key)
else
  redis.call('SET', waiting_key, room_id, 'EX', active_ttl)
end
redis.call('EXPIRE', room_key, active_ttl)
redis.call('EXPIRE', players_key, active_ttl)
return {room_id, state, tostring(count), tostring(deadline), tostring(should_start)}
"""

START_SCRIPT = r"""
local room_key = KEYS[1]
local waiting_key = KEYS[2]
local room_id = ARGV[1]
local started_at = ARGV[2]
if redis.call('HGET', room_key, 'state') ~= 'countdown' then return 0 end
redis.call('HSET', room_key, 'state', 'racing', 'startedAt', started_at)
if redis.call('GET', waiting_key) == room_id then redis.call('DEL', waiting_key) end
return 1
"""

PROGRESS_SCRIPT = r"""
local room_key = KEYS[1]
local players_key = KEYS[2]
local player_id = ARGV[1]
local player_json = ARGV[2]
local paragraph_length = tonumber(ARGV[3])
local now_ms = ARGV[4]
local completed_ttl = tonumber(ARGV[5])
if redis.call('HGET', room_key, 'state') ~= 'racing' then return {-1, 0, 0} end
local old_raw = redis.call('HGET', players_key, player_id)
if not old_raw then return {-2, 0, 0} end
local old_player = cjson.decode(old_raw)
local player = cjson.decode(player_json)
local placement = 0
if old_player.placement ~= nil and old_player.placement ~= cjson.null then
  placement = tonumber(old_player.placement)
end
if placement > 0 then player.placement = placement end
if tonumber(player.typedChars or 0) >= paragraph_length and placement == 0 then
  placement = redis.call('HINCRBY', room_key, 'finishCounter', 1)
  player.placement = placement
end
redis.call('HSET', players_key, player_id, cjson.encode(player))
local all_finished = 1
local connected_count = 0
local values = redis.call('HVALS', players_key)
for _, raw in ipairs(values) do
  local candidate = cjson.decode(raw)
  if candidate.connected then
    connected_count = connected_count + 1
    if candidate.placement == nil or candidate.placement == cjson.null or tonumber(candidate.placement) == 0 then
      all_finished = 0
    end
  end
end
local finalized = 0
if connected_count > 0 and all_finished == 1 and redis.call('HGET', room_key, 'state') == 'racing' then
  redis.call('HSET', room_key, 'state', 'finished', 'finishedAt', now_ms)
  redis.call('EXPIRE', room_key, completed_ttl)
  redis.call('EXPIRE', players_key, completed_ttl)
  finalized = 1
end
return {placement, all_finished, finalized}
"""

DISCONNECT_SCRIPT = r"""
local room_key = KEYS[1]
local players_key = KEYS[2]
local waiting_key = KEYS[3]
local room_id = ARGV[1]
local player_id = ARGV[2]
local now_ms = ARGV[3]
local completed_ttl = tonumber(ARGV[4])
local active_ttl = tonumber(ARGV[5])
local raw = redis.call('HGET', players_key, player_id)
if not raw then return {'missing', 0, 0} end
local player = cjson.decode(raw)
player.connected = false
local state = redis.call('HGET', room_key, 'state') or 'missing'
local count = tonumber(redis.call('HGET', room_key, 'playerCount') or '0')
if state == 'waiting' or state == 'countdown' then
  redis.call('HDEL', players_key, player_id)
  count = math.max(0, count - 1)
  redis.call('HSET', room_key, 'playerCount', count)
  if count < 2 then
    state = 'waiting'
    redis.call('HSET', room_key, 'state', state, 'countdownDeadline', 0)
  end
  if count > 0 then redis.call('SET', waiting_key, room_id, 'EX', active_ttl) end
else
  redis.call('HSET', players_key, player_id, cjson.encode(player))
end
if count == 0 then
  redis.call('DEL', room_key, players_key)
  if redis.call('GET', waiting_key) == room_id then redis.call('DEL', waiting_key) end
  return {state, count, 0}
end
local all_finished = 1
local connected_count = 0
local values = redis.call('HVALS', players_key)
for _, candidate_raw in ipairs(values) do
  local candidate = cjson.decode(candidate_raw)
  if candidate.connected then
    connected_count = connected_count + 1
    if candidate.placement == nil or candidate.placement == cjson.null or tonumber(candidate.placement) == 0 then all_finished = 0 end
  end
end
local finalized = 0
if state == 'racing' and connected_count > 0 and all_finished == 1 then
  state = 'finished'
  redis.call('HSET', room_key, 'state', state, 'finishedAt', now_ms)
  redis.call('EXPIRE', room_key, completed_ttl)
  redis.call('EXPIRE', players_key, completed_ttl)
  finalized = 1
end
return {state, count, finalized}
"""


class RedisRoomRepository:
    def __init__(
        self,
        redis_url: str,
        namespace: str = "typescale",
        active_ttl: int = 3600,
        completed_ttl: int = 900,
    ) -> None:
        self.redis: Any = Redis.from_url(redis_url, decode_responses=True)
        self.namespace = namespace
        self.active_ttl = active_ttl
        self.completed_ttl = completed_ttl
        self._join = self.redis.register_script(JOIN_SCRIPT)
        self._start = self.redis.register_script(START_SCRIPT)
        self._progress = self.redis.register_script(PROGRESS_SCRIPT)
        self._disconnect = self.redis.register_script(DISCONNECT_SCRIPT)

    @property
    def waiting_key(self) -> str:
        return f"{self.namespace}:waiting"

    @property
    def room_prefix(self) -> str:
        return f"{self.namespace}:room:"

    @property
    def event_pattern(self) -> str:
        return f"{self.room_prefix}*:events"

    def room_key(self, room_id: str) -> str:
        return f"{self.room_prefix}{room_id}"

    def players_key(self, room_id: str) -> str:
        return f"{self.room_key(room_id)}:players"

    def event_channel(self, room_id: str) -> str:
        return f"{self.room_key(room_id)}:events"

    async def ping(self) -> bool:
        return bool(await self.redis.ping())

    async def close(self) -> None:
        await self.redis.aclose()

    async def join(self, player: Dict, paragraph: str) -> Tuple[Dict, bool]:
        now_ms = int(time.time() * 1000)
        new_room_id = str(uuid.uuid4())
        result = await self._join(
            keys=[self.waiting_key],
            args=[
                self.room_prefix,
                new_room_id,
                paragraph,
                player["playerId"],
                json.dumps(player, separators=(",", ":")),
                now_ms,
                self.active_ttl,
            ],
        )
        room_id, _, _, _, should_start = result
        snapshot = await self.snapshot(room_id)
        return snapshot, should_start == "1"

    async def room(self, room_id: str) -> Optional[Dict]:
        data = await self.redis.hgetall(self.room_key(room_id))
        if not data:
            return None
        for field in (
            "playerCount",
            "finishCounter",
            "createdAt",
            "countdownDeadline",
            "startedAt",
            "finishedAt",
        ):
            if field in data:
                data[field] = int(data[field] or 0)
        return data

    async def players(self, room_id: str) -> List[Dict]:
        values = await self.redis.hvals(self.players_key(room_id))
        return [json.loads(value) for value in values]

    async def snapshot(self, room_id: str) -> Dict:
        room = await self.room(room_id)
        if room is None:
            raise KeyError(f"Room {room_id} does not exist")
        room["players"] = await self.players(room_id)
        return room

    async def start_race(self, room_id: str, started_at_ms: int) -> bool:
        result = await self._start(
            keys=[self.room_key(room_id), self.waiting_key],
            args=[room_id, started_at_ms],
        )
        return bool(result)

    async def update_progress(
        self, room_id: str, player_id: str, player: Dict, paragraph_length: int
    ) -> Tuple[Dict, bool]:
        result = await self._progress(
            keys=[self.room_key(room_id), self.players_key(room_id)],
            args=[
                player_id,
                json.dumps(player, separators=(",", ":")),
                paragraph_length,
                int(time.time() * 1000),
                self.completed_ttl,
            ],
        )
        if int(result[0]) < 0:
            raise RuntimeError("Race is not active or player is missing")
        snapshot = await self.snapshot(room_id)
        return snapshot, bool(int(result[2]))

    async def disconnect(self, room_id: str, player_id: str) -> Tuple[Optional[Dict], bool]:
        result = await self._disconnect(
            keys=[self.room_key(room_id), self.players_key(room_id), self.waiting_key],
            args=[
                room_id,
                player_id,
                int(time.time() * 1000),
                self.completed_ttl,
                self.active_ttl,
            ],
        )
        if int(result[1]) == 0:
            return None, False
        return await self.snapshot(room_id), bool(int(result[2]))

    async def publish(self, room_id: str, event: Dict) -> None:
        payload = dict(event)
        payload["roomId"] = room_id
        await self.redis.publish(
            self.event_channel(room_id), json.dumps(payload, separators=(",", ":"))
        )
