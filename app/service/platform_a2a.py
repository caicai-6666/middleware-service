"""平台 A2A 请求编排：目标授权、发布指纹、任务元数据及交付响应。"""

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Literal, get_args
from uuid import uuid4

from pydantic import BaseModel, Field, ValidationError, model_validator

from app.config.platform import Platform
from app.infrastructure.redis_platform_a2a import (
    RedisPlatformA2AStore, PlatformA2AStorageUnavailableError,
)


PlatformA2AReplyStatus = Literal['succeeded', 'not_applicable', 'context_expired', 'failed']


class InvalidPlatformA2AReplyError(ValueError):
    """反馈状态或不受理反馈的上下文组合不合法。"""


def validate_platform_a2a_reply(status: PlatformA2AReplyStatus, response_id: str | None) -> None:
    if status not in get_args(PlatformA2AReplyStatus):
        raise InvalidPlatformA2AReplyError('反馈状态必须为 succeeded、not_applicable、context_expired 或 failed')
    # 不受理是明确的业务反馈，不能同时给出可续聊上下文；不从消息文本或空 ID 猜测状态。
    if status == 'not_applicable' and response_id is not None:
        raise InvalidPlatformA2AReplyError('not_applicable 不受理反馈不能携带 response_id')


class InvalidPlatformA2ATargetError(Exception):
    """目标不在已登记的 A2A 子管理平台范围内。"""


class PlatformA2AForbiddenError(Exception):
    """调用平台角色不允许当前操作。"""


class PlatformA2ATaskReference(BaseModel):
    task_id: str = Field(description="中间件为本轮请求的一个目标平台生成的唯一任务标识。")
    target_platform_code: str = Field(description="该任务唯一接收方的平台代码。")


class PublishPlatformA2AResponse(BaseModel):
    status: Literal["published"] = Field(default="published", description="Redis 已返回整轮请求入队成功，或命中原发布的幂等记录，不表示子平台处理成功。")
    duplicate: bool = Field(description="是否为相同广播 ID 及相同内容的发布重试；重试不新增任务。")
    broadcast_id: str = Field(description="顶层指定的本轮唯一标识，用于关联所有目标平台任务及后续回复。")
    userid: str = Field(description="本轮消息所属用户的唯一标识。")
    source_platform_code: str = Field(description="从平台认证获取的来源平台代码。")
    target_platform_codes: list[str] = Field(description="本轮固定的实际目标平台代码列表，按代码排序。")
    reply_flow: Literal['broadcast', 'direct'] = Field(description="原请求未携带 response_id 时为 broadcast 聚合反馈，携带时为 direct 单聊反馈；首次单聊也为 broadcast。")
    created_at: datetime = Field(description="本轮首次成功入队的 Redis 服务端 UTC 时间；发布重试保持不变。")
    tasks: list[PlatformA2ATaskReference] = Field(description="本轮为每个目标创建的任务引用，重复发布返回原任务标识。")


class PlatformA2ATask(BaseModel):
    task_id: str = Field(description="中间件生成的具体任务唯一标识。")
    broadcast_id: str = Field(description="该任务所属的本轮广播、单聊或续聊标识。")
    userid: str = Field(description="请求所属用户，由子平台结合本地上下文执行用户权限校验。")
    source_platform_code: str = Field(description="发布请求的已认证顶层平台代码。")
    target_platform_code: str = Field(description="有权领取、处理该任务的子管理平台代码。")
    message: str = Field(description="顶层发布的本轮消息原文，不自动合并不同请求。")
    response_id: str | None = Field(description="要接续的历史响应标识；首次广播或单聊为 null，由子平台判断上下文有效性。")
    response_platform_code: str | None = Field(description="历史响应所属平台，续聊时与目标平台相同；首次消息为 null。")
    reply_flow: Literal['broadcast', 'direct'] = Field(description="由原请求是否包含历史 response_id 决定的反馈流，子平台不能通过本轮新响应标识改变分流。")
    created_at: datetime = Field(description="任务首次入队的 Redis 服务端 UTC 时间，重领不改变。")
    sequence: str = Field(description="按发布成功顺序递增的十进制序号，同一广播的不同目标共享序号；字符串避免整数精度丢失。")


class PlatformA2ATaskDelivery(BaseModel):
    task: PlatformA2ATask = Field(description="本次新领取或显式续租的任务，始终属于调用平台。")
    delivery_token: str = Field(repr=False, description="本次执行交付凭证，续租保持不变，租约过期重领后旧凭证失效。")
    lease_expires_at: datetime = Field(description="执行租约的 Redis 服务端 UTC 截止时间，到期不表示旧模型调用已经停止。")


class PlatformA2AWindow(BaseModel):
    duplicate: bool = Field(description="是否命中相同 Idempotency-Key 与相同参数的领取回执，重放不新领取或续租。")
    capacity: int = Field(description="本平台实际生效的执行窗口上限 k，优先使用平台 YAML 的 a2a_max_inflight，未配置时使用服务默认值。")
    inflight_count: int = Field(description="操作完成时当前消费队列租约仍有效的已领取条目总数，包含本平台所有用户和实例。")
    available_slots: int = Field(description="操作完成时剩余可领取名额；已用名额超过调低后的上限时为零。")


class PullPlatformA2AResponse(PlatformA2AWindow):
    tasks: list[PlatformA2ATaskDelivery] = Field(description="按本平台配置自动补齐剩余执行名额的新任务；队列为空或窗口已满时为空列表。")
    renewed_tasks: list[PlatformA2ATaskDelivery] = Field(description="本次明确指定且成功续租的任务，不占用新的并发名额。")


class PublishPlatformA2AReplyResponse(BaseModel):
    status: Literal["published"] = Field(default="published", description="Redis 已原子完成回复处理和原请求确认；已关闭广播的回复会被丢弃。")
    duplicate: bool = Field(description="是否为相同平台、任务、交付凭证和回复内容的完成重试，不会重复保存回复或释放名额。")
    task_id: str = Field(description="已经完成并从待处理和执行集合中清理的原请求任务标识。")
    broadcast_id: str = Field(description="原任务所属广播、单聊或续聊的本轮标识，从存储读取。")
    message_id: str = Field(description="中间件生成的反馈唯一标识，幂等重试返回原值；单聊反馈按此标识逐条确认。")
    reply_flow: Literal['broadcast', 'direct'] = Field(description="实际写入的反馈流，由原任务决定；broadcast 为广播聚合，direct 为持续领取的单聊反馈。")
    request_status: Literal["completed"] = Field(description="原请求已完成，不需要再调用独立的请求 ack。")
    reply_disposition: Literal["saved", "discarded"] = Field(description="saved 表示回复已写入对应反馈流；discarded 只用于广播收集已关闭的迟到回复，原请求仍正常完成。")
    completed_at: datetime = Field(description="首次完成任务时的 Redis 服务端 UTC 时间，重复提交保持不变。")


class PlatformA2AReply(BaseModel):
    message_id: str = Field(description="本条反馈唯一标识，单聊反馈领取后以该标识确认。")
    task_id: str = Field(description="产生本条反馈的原请求任务标识，一次任务只生成一个最终反馈。")
    broadcast_id: str = Field(description="原请求的本轮关联 ID，首次请求用于聚合，续聊请求用于追踪而不用于广播读取。")
    userid: str = Field(description="原请求所属用户，用于顶层分发到对应用户的处理流程。")
    platform_code: str = Field(description="给出反馈及响应标识的子管理平台代码，从原任务和认证身份关联。")
    status: PlatformA2AReplyStatus = Field(description="本轮最终反馈：succeeded 已答复、not_applicable 不受理、context_expired 上下文过期、failed 处理失败；不包含顶层本地生成的 timeout。")
    response_id: str | None = Field(description="子平台本次产生的续聊响应标识；not_applicable 必须为 null，succeeded 兼容 null，但不能据此推断为不受理。")
    message: str = Field(description="子平台返回的业务答复，或不受理、失败、上下文过期的说明。")
    created_at: datetime = Field(description="首次保存本条反馈的 Redis 服务端 UTC 时间。")

    @model_validator(mode='after')
    def validate_rejection(self):
        validate_platform_a2a_reply(self.status, self.response_id)
        return self


class PullPlatformA2ABroadcastResponse(BaseModel):
    broadcast_id: str = Field(description="所查询的首次广播或首次单聊的关联 ID。")
    userid: str = Field(description="该轮请求所属用户。")
    state: Literal['open', 'closed'] = Field(description="open 表示仍收集反馈；closed 表示整轮已确认，不再收集。")
    target_platform_codes: list[str] = Field(description="本轮发布时固定的全部目标平台。")
    pending_platform_codes: list[str] = Field(description="开放广播中尚未反馈的平台；关闭后为空列表，表示不再等待。")
    replies: list[PlatformA2AReply] = Field(description="当前已收到的反馈，按目标平台代码排序；读取不删除，关闭后为空列表。")


class AcknowledgePlatformA2ABroadcastResponse(BaseModel):
    broadcast_id: str = Field(description="已确认关闭的广播关联 ID。")
    state: Literal['closed'] = Field(default='closed', description="整轮反馈收集已关闭，迟到回复会丢弃。")
    duplicate: bool = Field(description="是否为已经关闭广播的重复确认。")
    closed_at: datetime = Field(description="首次关闭广播的 Redis 服务端 UTC 时间，重复确认不改变。")


class PlatformA2ADirectReplyDelivery(BaseModel):
    message: PlatformA2AReply = Field(description="已领取或显式续租的单聊最终反馈。")
    delivery_token: str = Field(repr=False, description="顶层领取反馈时获得的凭证，用于确认或续租；与子平台执行请求的凭证不同。")
    lease_expires_at: datetime = Field(description="顶层持有该反馈的 Redis 服务端 UTC 租约截止时间，过期可重领。")


class PullPlatformA2ADirectRepliesResponse(PlatformA2AWindow):
    messages: list[PlatformA2ADirectReplyDelivery] = Field(description="本次自动补齐顶层窗口后新领取的单聊反馈，空队列或满窗口时为空。")
    renewed_messages: list[PlatformA2ADirectReplyDelivery] = Field(description="本次明确指定并续租成功的单聊反馈。")


class AcknowledgePlatformA2ADirectReplyResponse(BaseModel):
    message_id: str = Field(description="已消费并从单聊反馈队列清理的反馈标识。")
    status: Literal['acknowledged'] = Field(default='acknowledged', description="顶层已声明完成处理或丢弃该反馈，窗口已释放一个名额。")
    duplicate: bool = Field(description="是否为同一消息和同一领取凭证的确认重试，不会重复释放名额。")
    acknowledged_at: datetime = Field(description="首次确认反馈的 Redis 服务端 UTC 时间。")


class PlatformA2AService:
    def __init__(self, store: RedisPlatformA2AStore, platforms: Mapping[str, Platform]):
        self._store = store
        self._platforms = platforms

    def _is_target(self, code: str) -> bool:
        platform = self._platforms.get(code)
        return bool(code != "knowledge-accumulation" and platform is not None
                    and platform.supports_a2a and platform.is_sub_management_platform)

    @staticmethod
    def _fingerprint(value) -> str:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode()).hexdigest()

    @staticmethod
    def _timestamp(milliseconds: int) -> datetime:
        seconds, remaining = divmod(milliseconds, 1000)
        return datetime.fromtimestamp(seconds, timezone.utc).replace(microsecond=remaining * 1000)

    async def publish(self, source_platform_code: str, *, broadcast_id: str, userid: str,
                      message: str, target_platform_codes: list[str] | None = None,
                      response_id: str | None = None,
                      response_platform_code: str | None = None) -> PublishPlatformA2AResponse:
        if source_platform_code != "knowledge-accumulation":
            raise PlatformA2AForbiddenError()
        # 排序后计算指纹，目标列表顺序变化不构成新的发布内容；不解释模型上下文 ID。
        targets = sorted(target_platform_codes or [response_platform_code])
        if not targets or any(not self._is_target(code) for code in targets):
            raise InvalidPlatformA2ATargetError()
        identity = dict(source_platform_code=source_platform_code, broadcast_id=broadcast_id,
                        userid=userid, message=message, target_platform_codes=targets,
                        response_id=response_id, response_platform_code=response_platform_code)
        reply_flow = 'direct' if response_id is not None else 'broadcast'
        tasks = [dict(task_id=uuid4().hex, broadcast_id=broadcast_id, userid=userid,
                      source_platform_code=source_platform_code, target_platform_code=code,
                      message=message, response_id=response_id,
                      reply_flow=reply_flow,
                      response_platform_code=response_platform_code) for code in targets]
        metadata = dict(broadcast_id=broadcast_id, userid=userid,
                        reply_flow=reply_flow,
                        source_platform_code=source_platform_code, target_platform_codes=targets,
                        tasks=[dict(task_id=task['task_id'], target_platform_code=task['target_platform_code'])
                               for task in tasks])
        duplicate, saved = await self._store.publish(
            broadcast_id=broadcast_id, fingerprint=self._fingerprint(identity),
            metadata=metadata, tasks=tasks,
        )
        try:
            saved.setdefault('reply_flow', reply_flow)
            saved['created_at'] = self._timestamp(saved.pop('created_at_ms'))
            return PublishPlatformA2AResponse(duplicate=duplicate, **saved)
        except (ValueError, TypeError, KeyError, OverflowError, ValidationError):
            raise PlatformA2AStorageUnavailableError() from None

    def _delivery(self, saved: dict) -> PlatformA2ATaskDelivery:
        task = saved['task']
        task.setdefault('reply_flow', 'direct' if task.get('response_id') is not None else 'broadcast')
        task['created_at'] = self._timestamp(task.pop('created_at_ms'))
        return PlatformA2ATaskDelivery(
            task=PlatformA2ATask(**task), delivery_token=saved['delivery_token'],
            lease_expires_at=self._timestamp(saved['lease_expires_at_ms']),
        )

    async def pull(self, platform_code: str, *, renew_tasks: list[dict],
                   request_id: str) -> PullPlatformA2AResponse:
        if not self._is_target(platform_code):
            raise PlatformA2AForbiddenError()
        ordered_renewals = sorted(renew_tasks, key=lambda task: task['task_id'])
        # 领取数量只由可信的平台配置和 Redis 中的有效执行记录决定，客户端不能改变窗口。
        capacity = (self._platforms[platform_code].a2a_max_inflight
                    or self._store.settings.max_inflight_per_platform)
        duplicate, saved, inflight = await self._store.pull(
            platform_code, capacity=capacity, renew_tasks=ordered_renewals, request_id=request_id,
            fingerprint=self._fingerprint(dict(renew_tasks=ordered_renewals)),
        )
        try:
            return PullPlatformA2AResponse(
                duplicate=duplicate, capacity=capacity, inflight_count=inflight,
                available_slots=max(0, capacity - inflight),
                tasks=[self._delivery(task) for task in saved['tasks']],
                renewed_tasks=[self._delivery(task) for task in saved['renewed_tasks']],
            )
        except (ValueError, TypeError, KeyError, OverflowError, ValidationError):
            raise PlatformA2AStorageUnavailableError() from None

    async def publish_reply(self, platform_code: str, *, task_id: str, delivery_token: str,
                            status: PlatformA2AReplyStatus,
                            message: str, response_id: str | None = None) -> PublishPlatformA2AReplyResponse:
        if not self._is_target(platform_code):
            raise PlatformA2AForbiddenError()
        # 服务层同样校验，避免非 HTTP 调用绕过约束后写入反馈并提前释放任务名额。
        validate_platform_a2a_reply(status, response_id)
        reply = dict(status=status, message=message, response_id=response_id)
        # 指纹包括交付凭证，旧执行者不能冒用同一 task_id 改写已提交结果。
        fingerprint = self._fingerprint(dict(task_id=task_id, delivery_token=delivery_token, reply=reply))
        duplicate, saved = await self._store.complete_with_reply(
            platform_code, task_id=task_id, delivery_token=delivery_token,
            fingerprint=fingerprint, reply=reply,
        )
        try:
            # 历史完成回执没有分流字段；其反馈原本全部保存在广播聚合集合。
            saved.setdefault('reply_flow', 'broadcast')
            saved.setdefault('message_id', saved['task_id'])
            saved['completed_at'] = self._timestamp(saved.pop('completed_at_ms'))
            return PublishPlatformA2AReplyResponse(duplicate=duplicate, **saved)
        except (ValueError, TypeError, KeyError, OverflowError, ValidationError):
            raise PlatformA2AStorageUnavailableError() from None

    @staticmethod
    def _require_top(platform_code: str):
        if platform_code != 'knowledge-accumulation':
            raise PlatformA2AForbiddenError()

    def _reply(self, saved: dict) -> PlatformA2AReply:
        payload = dict(saved)
        payload['created_at'] = self._timestamp(payload.pop('created_at_ms'))
        return PlatformA2AReply(**payload)

    async def pull_broadcast(self, platform_code: str, broadcast_id: str) -> PullPlatformA2ABroadcastResponse:
        self._require_top(platform_code)
        saved = await self._store.read_broadcast(broadcast_id)
        try:
            saved['replies'] = [self._reply(reply) for reply in saved['replies']]
            return PullPlatformA2ABroadcastResponse(**saved)
        except (ValueError, TypeError, KeyError, OverflowError):
            raise PlatformA2AStorageUnavailableError() from None

    async def acknowledge_broadcast(self, platform_code: str, broadcast_id: str) -> AcknowledgePlatformA2ABroadcastResponse:
        self._require_top(platform_code)
        duplicate, closed_at = await self._store.close_broadcast(broadcast_id)
        try:
            return AcknowledgePlatformA2ABroadcastResponse(
                broadcast_id=broadcast_id, duplicate=duplicate, closed_at=self._timestamp(closed_at))
        except (ValueError, TypeError, KeyError, OverflowError):
            raise PlatformA2AStorageUnavailableError() from None

    def _direct_delivery(self, saved: dict) -> PlatformA2ADirectReplyDelivery:
        return PlatformA2ADirectReplyDelivery(
            message=self._reply(saved['task']['reply']), delivery_token=saved['delivery_token'],
            lease_expires_at=self._timestamp(saved['lease_expires_at_ms']))

    async def pull_direct_replies(self, platform_code: str, *, renew_messages: list[dict],
                                  request_id: str) -> PullPlatformA2ADirectRepliesResponse:
        self._require_top(platform_code)
        ordered = sorted(renew_messages, key=lambda item: item['message_id'])
        capacity = self._platforms[platform_code].a2a_max_inflight or self._store.settings.max_inflight_per_platform
        # 只复用底层有租约队列算法，顶层反馈和子平台请求的 Redis 键及窗口相互独立。
        renewals = [dict(task_id=item['message_id'], delivery_token=item['delivery_token']) for item in ordered]
        duplicate, saved, inflight = await self._store.pull_direct_replies(
            capacity=capacity, renew_tasks=renewals, request_id=request_id,
            fingerprint=self._fingerprint(dict(renew_messages=ordered)))
        try:
            return PullPlatformA2ADirectRepliesResponse(
                duplicate=duplicate, capacity=capacity, inflight_count=inflight,
                available_slots=max(0, capacity - inflight),
                messages=[self._direct_delivery(item) for item in saved['tasks']],
                renewed_messages=[self._direct_delivery(item) for item in saved['renewed_tasks']])
        except (ValueError, TypeError, KeyError, OverflowError):
            raise PlatformA2AStorageUnavailableError() from None

    async def acknowledge_direct_reply(self, platform_code: str, *, message_id: str,
                                       delivery_token: str) -> AcknowledgePlatformA2ADirectReplyResponse:
        self._require_top(platform_code)
        duplicate, saved = await self._store.acknowledge_direct_reply(message_id, delivery_token)
        try:
            return AcknowledgePlatformA2ADirectReplyResponse(
                duplicate=duplicate, message_id=saved['message_id'],
                acknowledged_at=self._timestamp(saved['acknowledged_at_ms']))
        except (ValueError, TypeError, KeyError, OverflowError):
            raise PlatformA2AStorageUnavailableError() from None
