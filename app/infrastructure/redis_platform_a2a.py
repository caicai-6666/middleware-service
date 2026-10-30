"""平台 A2A 独立存储：广播发布、平台窗口与回复写入后原子完成请求。"""

import hashlib
import json
from uuid import uuid4

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.config.platform_a2a import PlatformA2ASettings


class PlatformA2AStorageUnavailableError(Exception):
    """Redis 不可用、数据异常或写入结果不确定。"""


class PlatformA2ARequestConflictError(Exception):
    """发布、领取或任务完成幂等标识被用于不同内容。"""


class PlatformA2AQueueFullError(Exception):
    """至少一个目标平台已达到保留任务容量，整次广播不入队。"""


class PlatformA2ADeliveryConflictError(Exception):
    """任务、续租凭证或原领取批次已失效，不能执行或重放该次交付。"""


class PlatformA2ABroadcastNotFoundError(Exception):
    """指定的广播聚合记录不存在。"""


class PlatformA2AReplyFlowError(Exception):
    """定向单聊请求不能使用广播聚合读取或关闭接口。"""


# Lua 只有执行隔离，没有出错回滚；在首次写入前检查键类型并解析所需正文。
_CHECK_TYPES = """
local function check_type(key, expected)
    local actual = redis.call('TYPE', key).ok
    if actual ~= 'none' and actual ~= expected then
        error('platform a2a storage type mismatch')
    end
end
local function now_ms()
    local clock = redis.call('TIME')
    return tonumber(clock[1]) * 1000 + math.floor(tonumber(clock[2]) / 1000)
end
local function encode_array(items)
    if #items == 0 then return '[]' end
    return cjson.encode(items)
end
"""

_PUBLISH = _CHECK_TYPES + """
check_type(KEYS[1], 'hash')
check_type(KEYS[2], 'string')
local previous = redis.call('HGET', KEYS[1], 'fingerprint')
if previous then
    if previous ~= ARGV[1] then return {'conflict'} end
    local saved = redis.call('HGET', KEYS[1], 'result')
    if not saved then error('platform a2a missing publish receipt') end
    return {'duplicate', saved}
end

local metadata = cjson.decode(ARGV[2])
local tasks = cjson.decode(ARGV[3])
local maximum = tonumber(ARGV[4])
for i, task in ipairs(tasks) do
    local pending_key = KEYS[2 * i + 1]
    local tasks_key = KEYS[2 * i + 2]
    check_type(pending_key, 'zset')
    check_type(tasks_key, 'hash')
    if redis.call('HLEN', tasks_key) >= maximum then return {'full'} end
end

local sequence_raw = redis.call('GET', KEYS[2])
local sequence = tonumber(sequence_raw or '0')
if not sequence or sequence < 0 or sequence >= 9007199254740990
        or sequence ~= math.floor(sequence) then
    error('platform a2a invalid sequence')
end
local created_at = now_ms()
metadata.created_at_ms = created_at
local encoded_tasks = {}
for i, task in ipairs(tasks) do
    task.created_at_ms = created_at
    task.sequence = string.format('%.0f', sequence + 1)
    encoded_tasks[i] = cjson.encode(task)
end
local result = cjson.encode(metadata)

-- 广播容量检查全部通过后才统一写入，不产生部分目标成功的半次广播。
redis.call('INCR', KEYS[2])
for i, task in ipairs(tasks) do
    redis.call('HSET', KEYS[2 * i + 2], task.task_id, encoded_tasks[i])
    redis.call('ZADD', KEYS[2 * i + 1], sequence + 1, task.task_id)
end
redis.call('HSET', KEYS[1], 'fingerprint', ARGV[1], 'result', result,
           'state', 'open', 'reply_flow', metadata.reply_flow)
return {'published', result}
"""

_PULL = _CHECK_TYPES + """
check_type(KEYS[1], 'zset')
check_type(KEYS[2], 'hash')
check_type(KEYS[3], 'zset')
check_type(KEYS[4], 'hash')
check_type(KEYS[5], 'hash')
local now = now_ms()
local maximum = tonumber(ARGV[1])
local deadline = now + tonumber(ARGV[2])
local function encode_array(items)
    if #items == 0 then return '[]' end
    return cjson.encode(items)
end
local function encode_result(tasks, renewed)
    return '{"tasks":' .. encode_array(tasks) .. ',"renewed_tasks":' .. encode_array(renewed) .. '}'
end
local previous = redis.call('HGET', KEYS[5], 'fingerprint')
if previous then
    if previous ~= ARGV[5] then return {'conflict'} end
    local saved = cjson.decode(redis.call('HGET', KEYS[5], 'result'))
    for _, deliveries in ipairs({saved.tasks, saved.renewed_tasks}) do
        for _, delivery in ipairs(deliveries) do
            local task_id = delivery.task.task_id
            local expires = tonumber(redis.call('ZSCORE', KEYS[3], task_id) or '0')
            if expires <= now or redis.call('HGET', KEYS[4], task_id) ~= delivery.delivery_token then
                return {'delivery_conflict'}
            end
            local encoded = redis.call('HGET', KEYS[2], task_id)
            if not encoded then error('platform a2a missing replayed task') end
            delivery.task = cjson.decode(encoded)
            delivery.lease_expires_at_ms = expires
        end
    end
    -- 回执重放只返回原来的交付，不续租、不额外占用名额。
    local count = redis.call('ZCOUNT', KEYS[3], '(' .. now, '+inf')
    return {'duplicate', encode_result(saved.tasks, saved.renewed_tasks), count}
end

local requested_renewals = cjson.decode(ARGV[3])
local tokens = cjson.decode(ARGV[4])
local renewed = {}
for _, delivery in ipairs(requested_renewals) do
    local expires = tonumber(redis.call('ZSCORE', KEYS[3], delivery.task_id) or '0')
    if expires <= now or redis.call('HGET', KEYS[4], delivery.task_id) ~= delivery.delivery_token then
        return {'delivery_conflict'}
    end
    local encoded = redis.call('HGET', KEYS[2], delivery.task_id)
    if not encoded then error('platform a2a missing renewed task') end
    renewed[#renewed + 1] = {task=cjson.decode(encoded), delivery_token=delivery.delivery_token,
                           lease_expires_at_ms=deadline}
end

local expired = redis.call('ZRANGEBYSCORE', KEYS[3], '-inf', now)
local inflight = redis.call('ZCARD', KEYS[3]) - #expired
local count = math.max(0, maximum - inflight)
local candidates = {}
local expired_tasks = {}
local function candidate(task_id)
    local encoded = redis.call('HGET', KEYS[2], task_id)
    if not encoded then error('platform a2a missing pending task') end
    local task = cjson.decode(encoded)
    if task.task_id ~= task_id or not tonumber(task.sequence) then
        error('platform a2a invalid task')
    end
    return task
end
for _, task_id in ipairs(expired) do
    local task = candidate(task_id)
    expired_tasks[#expired_tasks + 1] = task
    candidates[#candidates + 1] = task
end
if count > 0 then
    for _, task_id in ipairs(redis.call('ZRANGE', KEYS[1], 0, count - 1)) do
        candidates[#candidates + 1] = candidate(task_id)
    end
end
table.sort(candidates, function(a, b)
    if a.sequence == b.sequence then return a.task_id < b.task_id end
    return tonumber(a.sequence) < tonumber(b.sequence)
end)
local tasks = {}
for i = 1, math.min(count, #candidates) do
    tasks[i] = {task=candidates[i], delivery_token=tokens[i], lease_expires_at_ms=deadline}
end
local result = encode_result(tasks, renewed)
local function references(deliveries)
    local refs = {}
    for _, delivery in ipairs(deliveries) do
        refs[#refs + 1] = {task={task_id=delivery.task.task_id}, delivery_token=delivery.delivery_token}
    end
    return refs
end
-- 回执只保留任务引用和凭证，不为每次领取重复保存消息正文。
local receipt = encode_result(references(tasks), references(renewed))

-- 在凭证、正文和编码全部通过检查后才更改状态；一项续租失败时整次操作不生效。
for _, task in ipairs(expired_tasks) do
    redis.call('ZREM', KEYS[3], task.task_id)
    redis.call('HDEL', KEYS[4], task.task_id)
    redis.call('ZADD', KEYS[1], tonumber(task.sequence), task.task_id)
end
for _, delivery in ipairs(renewed) do
    redis.call('ZADD', KEYS[3], deadline, delivery.task.task_id)
end
for _, delivery in ipairs(tasks) do
    redis.call('ZREM', KEYS[1], delivery.task.task_id)
    redis.call('ZADD', KEYS[3], deadline, delivery.task.task_id)
    redis.call('HSET', KEYS[4], delivery.task.task_id, delivery.delivery_token)
end
redis.call('HSET', KEYS[5], 'fingerprint', ARGV[5], 'result', receipt)
redis.call('EXPIRE', KEYS[5], tonumber(ARGV[6]))
return {'ok', result, inflight + #tasks}
"""


_COMPLETE_WITH_REPLY = _CHECK_TYPES + """
check_type(KEYS[1], 'zset')
check_type(KEYS[2], 'hash')
check_type(KEYS[3], 'zset')
check_type(KEYS[4], 'hash')
check_type(KEYS[5], 'hash')
check_type(KEYS[6], 'hash')
check_type(KEYS[7], 'hash')
check_type(KEYS[8], 'zset')
check_type(KEYS[9], 'hash')
check_type(KEYS[10], 'string')
local previous = redis.call('HGET', KEYS[5], 'fingerprint')
if previous then
    if previous ~= ARGV[3] then return {'conflict'} end
    local saved = redis.call('HGET', KEYS[5], 'result')
    if not saved then error('platform a2a missing completion receipt') end
    return {'duplicate', saved}
end

local now = now_ms()
local expires = tonumber(redis.call('ZSCORE', KEYS[3], ARGV[1]) or '0')
if expires <= now or redis.call('HGET', KEYS[4], ARGV[1]) ~= ARGV[2] then
    return {'delivery_conflict'}
end
local encoded = redis.call('HGET', KEYS[2], ARGV[1])
if not encoded then return {'delivery_conflict'} end
local task = cjson.decode(encoded)
if task.task_id ~= ARGV[1] or task.target_platform_code ~= ARGV[5]
        or task.broadcast_id ~= ARGV[6] then
    error('platform a2a invalid completed task')
end
local state = redis.call('HGET', KEYS[6], 'state')
if state ~= 'open' and state ~= 'closed' then
    error('platform a2a missing or invalid broadcast state')
end
local reply = cjson.decode(ARGV[4])
-- 分流依据原请求，不依据本轮新产生的 response_id；首次单聊也进入广播聚合。
local flow = task.reply_flow
if not flow then
    flow = 'broadcast'
    if type(task.response_id) == 'string' then flow = 'direct' end
end
if flow ~= 'broadcast' and flow ~= 'direct' then error('platform a2a invalid reply flow') end
local sequence = 0
if flow == 'direct' then
    if redis.call('HLEN', KEYS[9]) >= tonumber(ARGV[9]) then return {'full'} end
    sequence = tonumber(redis.call('GET', KEYS[10]) or '0')
    if not sequence or sequence < 0 or sequence ~= math.floor(sequence)
            or sequence >= 9007199254740990 then error('platform a2a invalid direct sequence') end
end
reply.message_id = ARGV[8]
reply.task_id = task.task_id
reply.broadcast_id = task.broadcast_id
reply.userid = task.userid
reply.platform_code = task.target_platform_code
reply.created_at_ms = now
local reply_encoded = cjson.encode(reply)
local disposition = 'saved'
if flow == 'broadcast' and state == 'closed' then disposition = 'discarded' end
local result = cjson.encode({task_id=task.task_id, broadcast_id=task.broadcast_id,
                            message_id=reply.message_id, reply_flow=flow,
                            request_status='completed', reply_disposition=disposition,
                            completed_at_ms=now})
local envelope = nil
if flow == 'direct' then
    -- 复用有租约队列的内部信封结构；信封 task_id 是反馈 ID，正文 task_id 仍指原请求。
    envelope = cjson.encode({task_id=reply.message_id, sequence=string.format('%.0f', sequence + 1),
                             reply=reply})
end

-- 回复保存（或已关闭广播的丢弃）、任务删除、窗口释放和回执在同一脚本内完成。
-- 不提供独立 requests/ack，避免先释放名额、随后回复发布失败而丢失结果。
if flow == 'direct' then
    redis.call('INCR', KEYS[10])
    redis.call('HSET', KEYS[9], reply.message_id, envelope)
    redis.call('ZADD', KEYS[8], sequence + 1, reply.message_id)
elseif state == 'open' then
    redis.call('HSET', KEYS[7], task.task_id, reply_encoded)
end
redis.call('HSET', KEYS[6], 'reply_flow', flow)
redis.call('HDEL', KEYS[2], task.task_id)
redis.call('ZREM', KEYS[1], task.task_id)
redis.call('ZREM', KEYS[3], task.task_id)
redis.call('HDEL', KEYS[4], task.task_id)
redis.call('HSET', KEYS[5], 'fingerprint', ARGV[3], 'result', result)
redis.call('EXPIRE', KEYS[5], tonumber(ARGV[7]))
return {'completed', result}
"""


_BROADCAST_CONTEXT = """
local function broadcast_context()
    local state = redis.call('HGET', KEYS[1], 'state')
    if not state then return nil end
    if state ~= 'open' and state ~= 'closed' then error('platform a2a invalid broadcast state') end
    local metadata = cjson.decode(redis.call('HGET', KEYS[1], 'result'))
    -- 历史版本已收集的反馈继续作为聚合包读取，避免升级后遗留结果不可达。
    local flow = redis.call('HGET', KEYS[1], 'reply_flow') or metadata.reply_flow or 'broadcast'
    return metadata, state, flow
end
"""

_READ_BROADCAST = _CHECK_TYPES + _BROADCAST_CONTEXT + """
check_type(KEYS[1], 'hash')
check_type(KEYS[2], 'hash')
local metadata, state, flow = broadcast_context()
if not metadata then return {'not_found'} end
if flow ~= 'broadcast' then return {'wrong_flow'} end
local replies = {}
local pending = {}
if state == 'open' then
    for _, ref in ipairs(metadata.tasks) do
        local encoded = redis.call('HGET', KEYS[2], ref.task_id)
        if encoded then
            local reply = cjson.decode(encoded)
            if not reply.message_id then reply.message_id = reply.task_id end
            replies[#replies + 1] = reply
        else
            pending[#pending + 1] = ref.target_platform_code
        end
    end
end
local result = cjson.encode({broadcast_id=metadata.broadcast_id, userid=metadata.userid,
                            state=state, target_platform_codes=metadata.target_platform_codes})
result = string.sub(result, 1, -2) .. ',"replies":' .. encode_array(replies)
         .. ',"pending_platform_codes":' .. encode_array(pending) .. '}'
return {'ok', result}
"""

_CLOSE_BROADCAST = _CHECK_TYPES + _BROADCAST_CONTEXT + """
check_type(KEYS[1], 'hash')
check_type(KEYS[2], 'hash')
local metadata, state, flow = broadcast_context()
if not metadata then return {'not_found'} end
if flow ~= 'broadcast' then return {'wrong_flow'} end
if state == 'closed' then
    local closed_at = tonumber(redis.call('HGET', KEYS[1], 'closed_at_ms'))
    if not closed_at then error('platform a2a missing broadcast close time') end
    return {'duplicate', tostring(closed_at)}
end
local closed_at = now_ms()
-- 整轮确认同时封闭收集和清理回复，写入方在同一 Redis 上检查状态，迟到结果不会复活。
redis.call('HSET', KEYS[1], 'state', 'closed', 'closed_at_ms', closed_at)
redis.call('DEL', KEYS[2])
return {'closed', tostring(closed_at)}
"""

_ACK_DIRECT_REPLY = _CHECK_TYPES + """
check_type(KEYS[1], 'zset')
check_type(KEYS[2], 'hash')
check_type(KEYS[3], 'zset')
check_type(KEYS[4], 'hash')
check_type(KEYS[5], 'hash')
local previous = redis.call('HGET', KEYS[5], 'token')
if previous then
    if previous ~= ARGV[2] then return {'delivery_conflict'} end
    local result = redis.call('HGET', KEYS[5], 'result')
    if not result then error('platform a2a missing direct ack receipt') end
    return {'duplicate', result}
end
local now = now_ms()
local expires = tonumber(redis.call('ZSCORE', KEYS[3], ARGV[1]) or '0')
if expires <= now or redis.call('HGET', KEYS[4], ARGV[1]) ~= ARGV[2] then
    return {'delivery_conflict'}
end
local encoded = redis.call('HGET', KEYS[2], ARGV[1])
if not encoded then return {'delivery_conflict'} end
local envelope = cjson.decode(encoded)
if envelope.task_id ~= ARGV[1] or envelope.reply.message_id ~= ARGV[1] then
    error('platform a2a invalid direct message')
end
local result = cjson.encode({message_id=ARGV[1], acknowledged_at_ms=now})
redis.call('HDEL', KEYS[2], ARGV[1])
redis.call('ZREM', KEYS[1], ARGV[1])
redis.call('ZREM', KEYS[3], ARGV[1])
redis.call('HDEL', KEYS[4], ARGV[1])
redis.call('HSET', KEYS[5], 'token', ARGV[2], 'result', result)
redis.call('EXPIRE', KEYS[5], tonumber(ARGV[3]))
return {'acknowledged', result}
"""


class RedisPlatformA2AStore:
    def __init__(self, client: Redis, key_prefix: str, settings: PlatformA2ASettings):
        self._client = client
        self._prefix = f"{key_prefix}:platform-a2a"
        self.settings = settings

    def _platform_keys(self, platform_code: str) -> list[str]:
        prefix = f"{self._prefix}:platform:{platform_code}"
        return [f"{prefix}:pending", f"{prefix}:tasks", f"{prefix}:inflight", f"{prefix}:tokens"]

    def _broadcast_key(self, broadcast_id: str) -> str:
        identity = hashlib.sha256(broadcast_id.encode()).hexdigest()
        return f"{self._prefix}:broadcast:{identity}"

    def _direct_keys(self) -> list[str]:
        return [f"{self._prefix}:direct-replies:{suffix}"
                for suffix in ('pending', 'tasks', 'inflight', 'tokens')]

    async def publish(self, *, broadcast_id: str, fingerprint: str,
                      metadata: dict, tasks: list[dict]) -> tuple[bool, dict]:
        keys = [self._broadcast_key(broadcast_id), f"{self._prefix}:sequence"]
        for task in tasks:
            keys.extend(self._platform_keys(task['target_platform_code'])[:2])
        try:
            result = await self._client.eval(
                _PUBLISH, len(keys), *keys, fingerprint,
                json.dumps(metadata, ensure_ascii=False), json.dumps(tasks, ensure_ascii=False),
                self.settings.max_tasks_per_platform,
            )
            if result[0] == 'conflict':
                raise PlatformA2ARequestConflictError()
            if result[0] == 'full':
                raise PlatformA2AQueueFullError()
            return result[0] == 'duplicate', json.loads(result[1])
        except (RedisError, ValueError, TypeError, KeyError, IndexError):
            raise PlatformA2AStorageUnavailableError() from None

    async def pull(self, platform_code: str, *, capacity: int, renew_tasks: list[dict],
                   request_id: str, fingerprint: str) -> tuple[bool, dict, int]:
        receipt_id = hashlib.sha256(request_id.encode()).hexdigest()
        keys = self._platform_keys(platform_code)
        keys.append(f"{self._prefix}:platform:{platform_code}:pull:{receipt_id}")
        return await self._pull_queue(keys, capacity, renew_tasks, fingerprint)

    async def _pull_queue(self, keys: list[str], capacity: int, renew_tasks: list[dict],
                          fingerprint: str) -> tuple[bool, dict, int]:
        try:
            result = await self._client.eval(
                _PULL, len(keys), *keys, capacity,
                self.settings.lease_seconds * 1000, json.dumps(renew_tasks),
                json.dumps([uuid4().hex for _ in range(capacity)]), fingerprint,
                self.settings.pull_receipt_ttl_seconds,
            )
            if result[0] == 'conflict':
                raise PlatformA2ARequestConflictError()
            if result[0] == 'delivery_conflict':
                raise PlatformA2ADeliveryConflictError()
            return result[0] == 'duplicate', json.loads(result[1]), int(result[2])
        except (RedisError, ValueError, TypeError, KeyError, IndexError):
            raise PlatformA2AStorageUnavailableError() from None

    async def complete_with_reply(self, platform_code: str, *, task_id: str,
                                  delivery_token: str, fingerprint: str,
                                  reply: dict) -> tuple[bool, dict]:
        keys = self._platform_keys(platform_code)
        receipt_id = hashlib.sha256(task_id.encode()).hexdigest()
        receipt_key = f"{self._prefix}:platform:{platform_code}:completed:{receipt_id}"
        try:
            # 事务读取保证并发完成时能看到原任务或完成回执之一，不能在两次独立读取间漏掉两者。
            # 这一步只定位广播键，脚本仍重新校验凭证、任务和广播，防止读后状态变化。
            async with self._client.pipeline(transaction=True) as pipe:
                pipe.hget(keys[1], task_id)
                pipe.hget(receipt_key, 'result')
                task, receipt = await pipe.execute()
            if not task and not receipt:
                raise PlatformA2ADeliveryConflictError()
            context = json.loads(receipt or task)
            broadcast_key = self._broadcast_key(context['broadcast_id'])
            keys.extend([receipt_key, broadcast_key, f"{broadcast_key}:replies"])
            keys.extend(self._direct_keys()[:2])
            keys.append(f"{self._prefix}:direct-replies:sequence")
            result = await self._client.eval(
                _COMPLETE_WITH_REPLY, len(keys), *keys, task_id, delivery_token, fingerprint,
                json.dumps(reply, ensure_ascii=False), platform_code, context['broadcast_id'],
                self.settings.completion_receipt_ttl_seconds,
                uuid4().hex, self.settings.max_tasks_per_platform,
            )
            if result[0] == 'conflict':
                raise PlatformA2ARequestConflictError()
            if result[0] == 'delivery_conflict':
                raise PlatformA2ADeliveryConflictError()
            if result[0] == 'full':
                raise PlatformA2AQueueFullError()
            return result[0] == 'duplicate', json.loads(result[1])
        except (RedisError, ValueError, TypeError, KeyError, IndexError):
            raise PlatformA2AStorageUnavailableError() from None

    async def read_broadcast(self, broadcast_id: str) -> dict:
        key = self._broadcast_key(broadcast_id)
        try:
            result = await self._client.eval(_READ_BROADCAST, 2, key, f"{key}:replies")
            self._check_broadcast_result(result)
            return json.loads(result[1])
        except (RedisError, ValueError, TypeError, KeyError, IndexError):
            raise PlatformA2AStorageUnavailableError() from None

    @staticmethod
    def _check_broadcast_result(result):
        if result[0] == 'not_found':
            raise PlatformA2ABroadcastNotFoundError()
        if result[0] == 'wrong_flow':
            raise PlatformA2AReplyFlowError()

    async def close_broadcast(self, broadcast_id: str) -> tuple[bool, int]:
        key = self._broadcast_key(broadcast_id)
        try:
            result = await self._client.eval(_CLOSE_BROADCAST, 2, key, f"{key}:replies")
            self._check_broadcast_result(result)
            return result[0] == 'duplicate', int(result[1])
        except (RedisError, ValueError, TypeError, KeyError, IndexError):
            raise PlatformA2AStorageUnavailableError() from None

    async def pull_direct_replies(self, *, capacity: int, renew_tasks: list[dict],
                                  request_id: str, fingerprint: str) -> tuple[bool, dict, int]:
        receipt_id = hashlib.sha256(request_id.encode()).hexdigest()
        keys = self._direct_keys() + [f"{self._prefix}:direct-replies:pull:{receipt_id}"]
        return await self._pull_queue(keys, capacity, renew_tasks, fingerprint)

    async def acknowledge_direct_reply(self, message_id: str, delivery_token: str) -> tuple[bool, dict]:
        receipt_id = hashlib.sha256(message_id.encode()).hexdigest()
        keys = self._direct_keys() + [f"{self._prefix}:direct-replies:ack:{receipt_id}"]
        try:
            result = await self._client.eval(_ACK_DIRECT_REPLY, len(keys), *keys,
                                             message_id, delivery_token, self.settings.completion_receipt_ttl_seconds)
            if result[0] == 'delivery_conflict':
                raise PlatformA2ADeliveryConflictError()
            return result[0] == 'duplicate', json.loads(result[1])
        except (RedisError, ValueError, TypeError, KeyError, IndexError):
            raise PlatformA2AStorageUnavailableError() from None
