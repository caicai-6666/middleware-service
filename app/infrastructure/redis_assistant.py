"""助理任务 Redis 存储：原子发布、单任务租约、恢复与幂等确认。"""

from urllib.parse import quote

from redis.asyncio import Redis
from redis.exceptions import RedisError


class AssistantStorageUnavailableError(Exception):
    """操作失败或结果不确定，不向调用方泄露 Redis 错误。"""


class AssistantRequestConflictError(Exception):
    """同一幂等标识被用于不同的任务内容。"""


class AssistantQueueFullError(Exception):
    """单用户保留任务数已达到上限，不静默淘汰已有消息。"""


class AssistantTaskBusyError(Exception):
    """任务正在被另一个平台处理，禁止并发交付后续任务。"""


class AssistantAcknowledgementConflictError(Exception):
    """任务、交付凭证、领取平台或租约与当前执行记录不匹配。"""


# 每次最多清理 100 条到期索引，避免单次脚本因历史积压长时间阻塞 Redis。
# 待处理任务的幂等记录没有截止时间，只有确认后才开始保留期。
_CLEANUP = """
local clock = redis.call('TIME')
local now = tonumber(clock[1]) * 1000 + math.floor(tonumber(clock[2]) / 1000)
local expired = redis.call('ZRANGEBYSCORE', KEYS[5], '-inf', now, 'LIMIT', 0, 100)
for _, key in ipairs(expired) do
    redis.call('HDEL', KEYS[4], key)
    redis.call('ZREM', KEYS[5], key)
end
"""

# 分数只保存优先级，同分成员以固定宽度递增序号开头，避免浮点组合分数丢失精度。
# INCR 后 GET 读取十进制字符串，不通过 Lua 浮点数格式化大序号。
_PUBLISH = _CLEANUP + """
if ARGV[1] ~= '' then
    local deadline = redis.call('ZSCORE', KEYS[5], ARGV[1])
    if deadline and tonumber(deadline) <= now then
        redis.call('HDEL', KEYS[4], ARGV[1])
        redis.call('ZREM', KEYS[5], ARGV[1])
    end
    local previous = redis.call('HGET', KEYS[4], ARGV[1])
    if previous then
        local record = cjson.decode(previous)
        if record.fingerprint ~= ARGV[2] then return {-1, '', 0} end
        return {0, record.payload, record.merged or 0}
    end
end
local incoming = cjson.decode(ARGV[5])
local timestamp = clock[1] .. '.' .. string.rep('0', 6 - string.len(clock[2])) .. clock[2]
local function member_for(task)
    return string.rep('0', 20 - string.len(task.sequence)) .. task.sequence .. ':' .. task.task_id
end
local function fragment(task, accepted_at)
    return {message_id=task.task_id, message=task.message, source_platform_code=task.source_platform_code,
            source_assistant_id=task.source_assistant_id,
            request_id=task.request_id, created_at_unix=accepted_at, files=task.files}
end
local function save_receipt(encoded, merged)
    if ARGV[1] ~= '' then
        redis.call('HSET', KEYS[4], ARGV[1],
                   cjson.encode({fingerprint=ARGV[2], payload=encoded, merged=merged}))
    end
end
if incoming.task_type == 'user_message' then
    local pending_id = redis.call('GET', KEYS[7])
    -- 兼容升级前尚未领取的用户消息；只检查该优先级最后一个候选，避免扫描整个队列。
    if not pending_id then
        local last = redis.call('ZREVRANGEBYSCORE', KEYS[1], ARGV[4], ARGV[4], 'LIMIT', 0, 1)
        if #last > 0 then pending_id = string.sub(last[1], 22) end
    end
    local raw = pending_id and redis.call('HGET', KEYS[2], pending_id)
    if raw then
        local pending = cjson.decode(raw)
        if pending.task_type == 'user_message' and not pending._claimed
            and redis.call('ZSCORE', KEYS[1], member_for(pending)) then
            local messages = pending.user_messages or {fragment(pending, pending.created_at_unix)}
            -- 按相邻原始消息的接收时间判断，使用整数微秒精确处理间隔边界。
            local last_seconds, last_fraction = string.match(messages[#messages].created_at_unix, '^(%d+)%.(%d+)$')
            local last_time = tonumber(last_seconds) * 1000000 + tonumber(string.sub(last_fraction .. '000000', 1, 6))
            local current_time = tonumber(clock[1]) * 1000000 + tonumber(clock[2])
            local gap = current_time - last_time
            local text_bytes = 0
            for _, item in ipairs(messages) do text_bytes = text_bytes + string.len(item.message) end
            local pending_files = pending.files or {}
            local incoming_files = incoming.files or {}
            if gap >= 0 and gap <= tonumber(ARGV[9]) * 1000000
                and #messages < tonumber(ARGV[7]) and text_bytes + string.len(incoming.message) <= tonumber(ARGV[8])
                and #pending_files + #incoming_files <= tonumber(ARGV[10]) then
                table.insert(messages, fragment(incoming, timestamp))
                pending.user_messages = messages
                pending.created_at_unix = timestamp
                for _, file in ipairs(incoming_files) do table.insert(pending_files, file) end
                if #pending_files > 0 then pending.files = pending_files end
                local scopes = pending._request_keys or {}
                if not pending._request_keys and pending._request_key ~= '' then
                    table.insert(scopes, pending._request_key)
                end
                if ARGV[1] ~= '' then table.insert(scopes, ARGV[1]) end
                pending._request_keys = scopes
                local encoded = cjson.encode(pending)
                redis.call('HSET', KEYS[2], pending_id, encoded)
                redis.call('SET', KEYS[7], pending_id)
                save_receipt(encoded, 1)
                return {1, encoded, 1}
            end
        end
    end
end
if redis.call('HLEN', KEYS[2]) >= tonumber(ARGV[6]) then return {-2, '', 0} end
redis.call('INCR', KEYS[3])
local sequence = redis.call('GET', KEYS[3])
incoming.sequence = sequence
incoming.created_at_unix = timestamp
incoming._request_key = ARGV[1]
if incoming.task_type == 'user_message' then
    incoming.user_messages = {fragment(incoming, timestamp)}
end
local encoded = cjson.encode(incoming)
redis.call('HSET', KEYS[2], ARGV[3], encoded)
redis.call('ZADD', KEYS[1], ARGV[4], member_for(incoming))
if incoming.task_type == 'user_message' then redis.call('SET', KEYS[7], ARGV[3]) end
-- 新任务到来后取消整键过期，防止仍有待处理任务时幂等索引被删除。
redis.call('PERSIST', KEYS[4])
redis.call('PERSIST', KEYS[5])
save_receipt(encoded, 0)
return {1, encoded, 0}
"""

# 过期任务恢复原始优先级和序号，再与所有等待任务重新排序；不会抢占有效租约。
_PULL = _CLEANUP + """
local running = redis.call('HGETALL', KEYS[6])
if #running > 0 then
    local state = {}
    for i = 1, #running, 2 do state[running[i]] = running[i+1] end
    local payload = redis.call('HGET', KEYS[2], state.task_id)
    if not payload then return {-2, '', '', 0} end
    if tonumber(state.expires_at) > now then
        if state.owner ~= ARGV[1] then return {-1, '', '', 0} end
        local deadline = now + tonumber(ARGV[3]) * 1000
        redis.call('HSET', KEYS[6], 'expires_at', deadline)
        return {1, payload, state.delivery_token, deadline}
    end
    local frozen = cjson.decode(payload)
    frozen._claimed = true
    redis.call('HSET', KEYS[2], state.task_id, cjson.encode(frozen))
    redis.call('ZADD', KEYS[1], state.score, state.member)
    redis.call('DEL', KEYS[6])
end
local first = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
if #first == 0 then return {0, '', '', 0} end
local task_id = string.sub(first[1], 22)
local payload = redis.call('HGET', KEYS[2], task_id)
if not payload then return {-2, '', '', 0} end
local frozen = cjson.decode(payload)
frozen._claimed = true
payload = cjson.encode(frozen)
redis.call('HSET', KEYS[2], task_id, payload)
if redis.call('GET', KEYS[7]) == task_id then redis.call('DEL', KEYS[7]) end
local deadline = now + tonumber(ARGV[3]) * 1000
redis.call('HSET', KEYS[6], 'task_id', task_id, 'member', first[1], 'score', first[2],
           'owner', ARGV[1], 'delivery_token', ARGV[2], 'expires_at', deadline)
redis.call('ZREM', KEYS[1], first[1])
return {1, payload, ARGV[2], deadline}
"""

# 确认凭证包括任务 ID、领取平台和交付 token；过期执行者不能确认后来重领的任务。
_ACK = _CLEANUP + """
local previous = redis.call('GET', KEYS[8])
if previous then
    local receipt = cjson.decode(previous)
    if receipt.owner == ARGV[1] and receipt.delivery_token == ARGV[3] then
        local files = receipt.files or {}
        return {2, #files > 0 and cjson.encode(files) or '[]'}
    end
    return {0, '[]'}
end
if redis.call('HGET', KEYS[6], 'task_id') ~= ARGV[2]
    or redis.call('HGET', KEYS[6], 'owner') ~= ARGV[1]
    or redis.call('HGET', KEYS[6], 'delivery_token') ~= ARGV[3]
    or tonumber(redis.call('HGET', KEYS[6], 'expires_at') or '0') <= now then return {0, '[]'} end
local raw = redis.call('HGET', KEYS[2], ARGV[2])
if not raw then return {0, '[]'} end
local task = cjson.decode(raw)
-- 只有有效心跳任务完成才记为助理心跳；生产者写入、重复确认和失败确认不算。
if task.task_type == 'heartbeat' then
    redis.call('SET', KEYS[9], clock[1] .. '.' .. string.rep('0', 6 - string.len(clock[2])) .. clock[2])
end
redis.call('HDEL', KEYS[2], ARGV[2])
redis.call('DEL', KEYS[6])
local scopes = task._request_keys or {}
if not task._request_keys and task._request_key ~= '' then table.insert(scopes, task._request_key) end
for _, key in ipairs(scopes) do
    redis.call('ZADD', KEYS[5], now + tonumber(ARGV[4]) * 1000, key)
end
local files = task.files or {}
redis.call('SET', KEYS[8], cjson.encode({owner=ARGV[1], delivery_token=ARGV[3], files=files}), 'EX', ARGV[4])
-- 队列已排空时整体设置保留期，空闲用户不需要后台扫描即可回收重试索引。
if redis.call('HLEN', KEYS[2]) == 0 then
    redis.call('EXPIRE', KEYS[4], ARGV[4])
    redis.call('EXPIRE', KEYS[5], ARGV[4])
end
return {1, #files > 0 and cjson.encode(files) or '[]'}
"""


# 只读快照不复用清理脚本，不续租、不恢复超时任务，也不改变历史心跳。
_STATUS = """
local clock = redis.call('TIME')
local now = tonumber(clock[1]) * 1000 + math.floor(tonumber(clock[2]) / 1000)
local heartbeat = redis.call('GET', KEYS[3]) or ''
local task_id = redis.call('HGET', KEYS[1], 'task_id')
local deadline = tonumber(redis.call('HGET', KEYS[1], 'expires_at') or '0')
if not task_id or deadline <= now then return {'', '', 0, heartbeat} end
local raw = redis.call('HGET', KEYS[2], task_id)
if not raw then return redis.error_reply('Assistant processing payload missing') end
local task = cjson.decode(raw)
return {task_id, task.task_type, deadline, heartbeat}
"""


class RedisAssistantTaskStore:
    def __init__(self, client: Redis, key_prefix: str, max_tasks_per_user: int = 1000,
                 lease_seconds: int = 300, receipt_ttl_seconds: int = 86400,
                 merge_max_messages: int = 20, merge_max_bytes: int = 65536,
                 merge_interval_seconds: int = 30, max_files_per_task: int = 10):
        self._client = client
        self._prefix = key_prefix
        self._max_tasks = max_tasks_per_user
        self._lease_seconds = lease_seconds
        self._receipt_ttl_seconds = receipt_ttl_seconds
        self._merge_max_messages = merge_max_messages
        self._merge_max_bytes = merge_max_bytes
        self._merge_interval_seconds = merge_interval_seconds
        self._max_files = max_files_per_task

    def keys(self, userid: str) -> list[str]:
        # 编码用户标识，防止原始大括号改变 hash tag 或分隔符产生歧义。
        root = f"{self._prefix}:assistant:{{{quote(userid, safe='')}}}:tasks"
        return [f"{root}:{suffix}" for suffix in
                ("ready", "payload", "sequence", "requests", "request-expiry", "processing", "pending-user-message")]

    async def _eval(self, script: str, keys: list[str], args: list):
        try:
            return await self._client.eval(script, len(keys), *keys, *args)
        except RedisError:
            raise AssistantStorageUnavailableError() from None

    async def publish(self, userid: str, *, idempotency_key: str, fingerprint: str,
                      task_id: str, priority: int, payload: str) -> tuple[bool, bool, str]:
        status, encoded, merged = await self._eval(_PUBLISH, self.keys(userid), [
            idempotency_key, fingerprint, task_id, priority, payload, self._max_tasks,
            self._merge_max_messages, self._merge_max_bytes, self._merge_interval_seconds, self._max_files,
        ])
        if status == -1:
            raise AssistantRequestConflictError()
        if status == -2:
            raise AssistantQueueFullError()
        return bool(status), bool(merged), encoded

    async def pull(self, userid: str, platform_code: str, delivery_token: str) -> tuple[str, str, int] | None:
        status, payload, token, deadline = await self._eval(
            _PULL, self.keys(userid), [platform_code, delivery_token, self._lease_seconds],
        )
        if status == -1:
            raise AssistantTaskBusyError()
        if status == -2:
            raise AssistantStorageUnavailableError()
        return (payload, token, deadline) if status else None

    async def acknowledge(self, userid: str, platform_code: str, task_id: str, delivery_token: str) -> tuple[bool, list[dict]]:
        import json
        keys = self.keys(userid)
        keys.append(f"{keys[0].removesuffix(':ready')}:ack:{quote(task_id, safe='')}")
        keys.append(self.heartbeat_key(userid))
        status, files = await self._eval(_ACK, keys, [
            platform_code, task_id, delivery_token, self._receipt_ttl_seconds,
        ])
        if not status:
            raise AssistantAcknowledgementConflictError()
        return status == 2, json.loads(files)

    def heartbeat_key(self, userid: str) -> str:
        return f"{self.keys(userid)[0].removesuffix(':ready')}:last-heartbeat"

    async def status(self, userid: str) -> tuple[str, str, int, str]:
        keys = self.keys(userid)
        return tuple(await self._eval(_STATUS, [keys[5], keys[1], self.heartbeat_key(userid)], []))
