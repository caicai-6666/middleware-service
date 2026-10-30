"""平台 A2A 统一请求入口、广播聚合反馈和带滑动窗口的单聊反馈。"""

from contextlib import contextmanager
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.router.dependencies import (
    AuthenticatedPlatform, require_knowledge_accumulation, require_platform,
)
from app.infrastructure.redis_platform_a2a import (
    PlatformA2ARequestConflictError, PlatformA2AQueueFullError, PlatformA2ADeliveryConflictError,
    PlatformA2ABroadcastNotFoundError, PlatformA2AReplyFlowError,
)
from app.service.platform_a2a import (
    PublishPlatformA2AResponse, PullPlatformA2AResponse, PublishPlatformA2AReplyResponse,
    InvalidPlatformA2ATargetError, PlatformA2AForbiddenError,
    PullPlatformA2ABroadcastResponse, AcknowledgePlatformA2ABroadcastResponse,
    PullPlatformA2ADirectRepliesResponse, AcknowledgePlatformA2ADirectReplyResponse,
    PlatformA2AReplyStatus, InvalidPlatformA2AReplyError, validate_platform_a2a_reply,
)


router = APIRouter(
    prefix="/platform-a2a", tags=["平台 A2A"],
    responses={
        401: {"description": "平台令牌缺失、无效或已过期"},
        403: {"description": "调用平台不具备当前操作的角色权限"},
    },
)

Identifier = Annotated[str, Field(min_length=1, max_length=128, pattern=r"\S")]
PlatformCode = Annotated[str, Field(pattern=r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")]


class PlatformA2ARequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PublishPlatformA2ARequest(PlatformA2ARequest):
    broadcast_id: Identifier = Field(description="顶层生成的本轮唯一标识；广播、首次单聊和续聊均使用新值，同次发布重试沿用原值。")
    userid: Identifier = Field(description="请求所属用户的唯一标识，用于用户隔离及权限判断，不代表已获得用户授权。")
    message: str = Field(min_length=1, max_length=10000, pattern=r"\S", description="本轮发送给目标子管理平台的消息，不能仅包含空白字符。")
    target_platform_codes: list[PlatformCode] | None = Field(default=None, min_length=1, max_length=100, description="广播或首次单聊的目标平台代码列表，最多 100 个已登记且支持 A2A 的子管理平台，不允许重复；单聊填写一个平台，与续聊字段互斥。")
    response_id: Identifier | None = Field(default=None, description="续聊所接续的历史响应标识，必须与 response_platform_code 同时提供。")
    response_platform_code: PlatformCode | None = Field(default=None, description="历史 response_id 所属平台的代码，也是本轮续聊的唯一目标。")

    @model_validator(mode="after")
    def validate_destination(self):
        has_response = self.response_id is not None
        has_platform = self.response_platform_code is not None
        if has_response != has_platform:
            raise ValueError("response_id 与 response_platform_code 必须同时提供")
        if (self.target_platform_codes is not None) == has_response:
            raise ValueError("必须且只能指定目标平台列表或续聊响应及所属平台")
        if self.target_platform_codes is not None:
            if len(set(self.target_platform_codes)) != len(self.target_platform_codes):
                raise ValueError("目标平台代码不能重复")
        return self


class PlatformA2ADelivery(PlatformA2ARequest):
    task_id: Identifier = Field(description="中间件为一个目标平台生成的具体任务标识。")
    delivery_token: Identifier = Field(repr=False, description="领取任务时获得的交付凭证，用于续租及提交回复；回复提交时自动完成原请求，重领后旧凭证失效。")


class PullPlatformA2ARequests(PlatformA2ARequest):
    renew_tasks: list[PlatformA2ADelivery] = Field(default_factory=list, max_length=100, description="需要续租的已领取任务及交付凭证，最多 100 项，默认空列表；任一凭证失效则整次操作失败，平台登录心跳不替代任务续租。")

    @model_validator(mode="after")
    def reject_duplicate_tasks(self):
        if len({task.task_id for task in self.renew_tasks}) != len(self.renew_tasks):
            raise ValueError("续租任务不能重复")
        return self


class PublishPlatformA2AReply(PlatformA2ADelivery):
    status: PlatformA2AReplyStatus = Field(description="本轮最终反馈：succeeded 已答复、not_applicable 不受理、context_expired 上下文过期、failed 处理失败；不受理也必须反馈，timeout 仅由顶层本地生成。")
    message: str = Field(min_length=1, max_length=10000, pattern=r"\S", description="业务答复，或不受理、失败、上下文过期的说明；所有反馈均必填，不能仅包含空白字符。")
    response_id: Identifier | None = Field(default=None, description="本轮可用于后续续聊的响应标识；not_applicable 必须省略或为 null，succeeded 为 null 时只表示未提供可续聊标识，不代表不受理。")

    @model_validator(mode='after')
    def validate_rejection(self):
        validate_platform_a2a_reply(self.status, self.response_id)
        return self


class PlatformA2ABroadcastRequest(PlatformA2ARequest):
    broadcast_id: Identifier = Field(description="未携带历史 response_id 的广播或首次单聊关联标识，用于聚合读取和整轮关闭；续聊关联 ID 不能用于该入口。")


class PlatformA2ADirectDelivery(PlatformA2ARequest):
    message_id: Identifier = Field(description="顶层已领取的单聊反馈唯一标识，来自反馈 message.message_id，不是子平台请求凭证。")
    delivery_token: Identifier = Field(repr=False, description="顶层领取该反馈时获得的凭证，用于反馈续租或逐条确认，超时重领后旧凭证失效。")


class PullPlatformA2ADirectRepliesRequest(PlatformA2ARequest):
    renew_messages: list[PlatformA2ADirectDelivery] = Field(default_factory=list, max_length=100, description="需续租的已领取单聊反馈及对应凭证，默认空列表，最多 100 条；续租成功后也会自动补齐顶层反馈窗口。")

    @model_validator(mode='after')
    def reject_duplicates(self):
        if len({item.message_id for item in self.renew_messages}) != len(self.renew_messages):
            raise ValueError('续租反馈不能重复')
        return self


async def require_a2a_sub_management_platform(
    request: Request,
    platform: Annotated[AuthenticatedPlatform, Depends(require_platform)],
) -> AuthenticatedPlatform:
    configured = request.app.state.platforms.get(platform.code)
    # 能力及角色限制只用于本模块，不更改其他业务接口的权限规则。
    if (platform.code == "knowledge-accumulation" or configured is None
            or not configured.supports_a2a or not configured.is_sub_management_platform):
        raise HTTPException(403, "仅支持 A2A 的子管理平台可以执行此操作")
    return platform


TopPlatform = Annotated[AuthenticatedPlatform, Depends(require_knowledge_accumulation)]
SubPlatform = Annotated[AuthenticatedPlatform, Depends(require_a2a_sub_management_platform)]


@contextmanager
def feedback_errors():
    """反馈消费接口共享稳定的业务错误映射，存储故障继续交给应用统一返回 503。"""
    try:
        yield
    except PlatformA2AForbiddenError:
        raise HTTPException(403, '仅 knowledge-accumulation 可以消费平台反馈') from None
    except PlatformA2ABroadcastNotFoundError:
        raise HTTPException(404, '广播关联 ID 不存在') from None
    except PlatformA2AReplyFlowError:
        raise HTTPException(409, '该请求属于单聊反馈流，请使用 direct-replies 接口') from None
    except PlatformA2ARequestConflictError:
        raise HTTPException(409, 'Idempotency-Key 已用于不同的领取参数') from None
    except PlatformA2ADeliveryConflictError:
        raise HTTPException(409, '反馈未领取、凭证不匹配、租约已过期或原领取批次已失效') from None


@router.post("/requests/publish", status_code=201, response_model=PublishPlatformA2AResponse,
             summary="发布平台 A2A 请求", description="向固定的目标平台集合原子发布广播、首次单聊或续聊请求；同广播 ID 相同内容重试不重复入队。",
             responses={409: {"description": "同一广播 ID 被用于不同内容"},
                        429: {"description": "至少一个目标平台达到任务容量，整次广播未发布"},
                        503: {"description": "存储不可用或结果不确定，沿用广播 ID 重试"}})
async def publish_requests(body: PublishPlatformA2ARequest, request: Request,
                           response: Response, platform: TopPlatform) -> PublishPlatformA2AResponse:
    response.headers['Cache-Control'] = 'no-store'
    try:
        return await request.app.state.platform_a2a.publish(platform.code, **body.model_dump())
    except PlatformA2AForbiddenError:
        raise HTTPException(403, "仅 knowledge-accumulation 可以发布平台 A2A 请求") from None
    except InvalidPlatformA2ATargetError:
        raise HTTPException(422, "目标必须是已登记且支持 A2A 的子管理平台") from None
    except PlatformA2ARequestConflictError:
        raise HTTPException(409, "broadcast_id 已用于不同的请求内容") from None
    except PlatformA2AQueueFullError:
        raise HTTPException(429, "至少一个目标平台的任务容量已满，本次广播未发布") from None


@router.post("/requests/pull", response_model=PullPlatformA2AResponse,
             summary="领取平台 A2A 请求", description="按本平台配置自动补齐执行窗口剩余名额，无需 limit 或请求体；可显式续租，相同 Idempotency-Key 重试只返回原交付。",
             responses={409: {"description": "领取幂等参数冲突、续租凭证失效或原领取批次已失效"},
                        503: {"description": "存储不可用或结果不确定，使用相同领取幂等键及参数重试"}})
async def pull_requests(
    request: Request, response: Response, platform: SubPlatform,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128,
                                          pattern=r"\S", description="本次领取操作的唯一标识，同次网络重试保持该值及参数不变；新领取或新续租必须使用新值，不能仅为空白。")],
    body: PullPlatformA2ARequests | None = None,
) -> PullPlatformA2AResponse:
    response.headers['Cache-Control'] = 'no-store'
    try:
        return await request.app.state.platform_a2a.pull(
            platform.code, renew_tasks=[task.model_dump() for task in body.renew_tasks] if body else [],
            request_id=idempotency_key,
        )
    except PlatformA2AForbiddenError:
        raise HTTPException(403, "仅支持 A2A 的子管理平台可以领取请求") from None
    except PlatformA2ARequestConflictError:
        raise HTTPException(409, "Idempotency-Key 已用于不同的领取参数") from None
    except PlatformA2ADeliveryConflictError:
        raise HTTPException(409, "续租凭证或原领取批次已失效，请停止使用旧凭证并以新幂等键重新领取") from None


@router.post("/replies/publish", status_code=201, response_model=PublishPlatformA2AReplyResponse,
             summary="发布平台 A2A 回复并完成请求", description="按原任务自动进入广播聚合或单聊反馈流，原子完成原请求并释放子平台名额；每个请求只返回一个最终结果。",
             responses={409: {"description": "任务未领取、已失效、凭证不匹配或已完成任务的重复提交内容不同"},
                        429: {"description": "单聊反馈队列容量已满，原请求未完成，请保留结果并续租后重试"},
                        503: {"description": "存储不可用或结果不确定，请用同一任务、凭证及回复内容重试"}})
async def publish_replies(body: PublishPlatformA2AReply, request: Request,
                          response: Response, platform: SubPlatform) -> PublishPlatformA2AReplyResponse:
    response.headers['Cache-Control'] = 'no-store'
    try:
        return await request.app.state.platform_a2a.publish_reply(platform.code, **body.model_dump())
    except PlatformA2AForbiddenError:
        raise HTTPException(403, "仅支持 A2A 的子管理平台可以提交回复") from None
    except InvalidPlatformA2AReplyError as error:
        raise HTTPException(422, str(error)) from None
    except PlatformA2ARequestConflictError:
        raise HTTPException(409, "该任务已完成，重复提交的交付凭证或回复内容不一致") from None
    except PlatformA2ADeliveryConflictError:
        raise HTTPException(409, "任务未领取、交付凭证不匹配、租约已过期或完成回执已失效") from None
    except PlatformA2AQueueFullError:
        raise HTTPException(429, '单聊反馈队列容量已满，原请求未完成，请保留结果并续租后重试') from None


@router.post("/replies/pull", response_model=PullPlatformA2ABroadcastResponse,
             summary="读取广播聚合反馈", description="按广播 ID 返回当前各平台的响应标识和结果，首次单聊也使用此入口；读取不关闭收集。",
             responses={404: {"description": "广播不存在"}, 409: {"description": "关联 ID 属于续聊，不能按广播读取"},
                        503: {"description": "反馈存储不可用或数据异常"}})
async def pull_replies(body: PlatformA2ABroadcastRequest, request: Request,
                       response: Response, platform: TopPlatform) -> PullPlatformA2ABroadcastResponse:
    response.headers['Cache-Control'] = 'no-store'
    with feedback_errors():
        return await request.app.state.platform_a2a.pull_broadcast(platform.code, body.broadcast_id)


@router.post("/replies/ack", response_model=AcknowledgePlatformA2ABroadcastResponse,
             summary="确认并关闭广播聚合反馈", description="幂等关闭整轮广播，清理已有回复并丢弃迟到回复，不影响之后的定向单聊反馈。",
             responses={404: {"description": "广播不存在"}, 409: {"description": "续聊请求不能作为广播关闭"},
                        503: {"description": "存储不可用或关闭结果不确定，请沿用广播 ID 重试"}})
async def acknowledge_replies(body: PlatformA2ABroadcastRequest, request: Request,
                              response: Response, platform: TopPlatform) -> AcknowledgePlatformA2ABroadcastResponse:
    response.headers['Cache-Control'] = 'no-store'
    with feedback_errors():
        return await request.app.state.platform_a2a.acknowledge_broadcast(platform.code, body.broadcast_id)


@router.post('/direct-replies/pull', response_model=PullPlatformA2ADirectRepliesResponse,
             summary='领取单聊反馈', description='顶层按自身配置自动补齐单聊反馈窗口，未确认反馈占用名额；普通领取无需请求体，可显式续租。',
             responses={409: {'description': '领取幂等冲突、反馈凭证或原批次失效'},
                        503: {'description': '存储不可用或领取结果不确定，请沿用幂等键重试'}})
async def pull_direct_replies(
    request: Request, response: Response, platform: TopPlatform,
    idempotency_key: Annotated[str, Header(alias='Idempotency-Key', min_length=1, max_length=128,
                                          pattern=r'\S', description='顶层本次单聊反馈领取的唯一标识，同次重试保持该值和续租参数不变，新操作使用新值。')],
    body: PullPlatformA2ADirectRepliesRequest | None = None,
) -> PullPlatformA2ADirectRepliesResponse:
    response.headers['Cache-Control'] = 'no-store'
    with feedback_errors():
        return await request.app.state.platform_a2a.pull_direct_replies(
            platform.code, renew_messages=[item.model_dump() for item in body.renew_messages] if body else [],
            request_id=idempotency_key)


@router.post('/direct-replies/ack', response_model=AcknowledgePlatformA2ADirectReplyResponse,
             summary='逐条确认单聊反馈', description='顶层处理或丢弃反馈后显式确认，清理该条反馈并释放一个名额；不关闭会话或影响其他反馈。',
             responses={409: {'description': '反馈未领取、凭证不匹配、过期或确认回执已失效'},
                        503: {'description': '存储不可用或确认结果不确定，请沿用反馈 ID 和凭证重试'}})
async def acknowledge_direct_reply(body: PlatformA2ADirectDelivery, request: Request,
                                   response: Response, platform: TopPlatform) -> AcknowledgePlatformA2ADirectReplyResponse:
    response.headers['Cache-Control'] = 'no-store'
    with feedback_errors():
        return await request.app.state.platform_a2a.acknowledge_direct_reply(platform.code, **body.model_dump())
