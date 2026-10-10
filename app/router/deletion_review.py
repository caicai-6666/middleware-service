"""删除审批结果发布、按平台过滤拉取及消费确认接口。"""

from contextlib import asynccontextmanager
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator

from app.router.dependencies import AuthenticatedPlatform, require_platform
from app.service.platform_session import (
    InvalidAccessTokenError, PlatformPublishForbiddenError, PlatformResourcesUnavailableError,
    PlatformConsumeForbiddenError,
)
from app.service.result_message import InvalidReviewTargetError, ReviewPublishError
from app.service.stream_deduplication import DuplicateStreamMessageError, StreamDeduplicationUnavailableError
from app.service.stream_consumer import (
    AcknowledgementConflictError, ConsumptionUnavailableError, InvalidStreamMessageError,
)


router = APIRouter(
    prefix="/deletion-results", tags=["删除审批结果"],
    responses={401: {"description": "令牌缺失、无效或已过期"}},
)
PlatformIdentity = Annotated[AuthenticatedPlatform, Depends(require_platform)]


class PublishDeletionReviewRequest(BaseModel):
    model_config = {"extra": "forbid"}

    request_id: str = Field(min_length=1, max_length=128, description="原删除申请的 message_id，与目标子平台代码组成去重键；该平台尚未确认同一申请的结果时拒绝再次发布。")
    target_platform_code: str = Field(min_length=1, description="接收删除审批结果的子平台代号，不表示调用方身份。")
    approved: bool = Field(strict=True, description="是否批准删除：true 为批准，false 为拒绝，不代表已经完成删除。")
    note: str = Field(max_length=10000, description="注册中心的删除审批意见，允许为空字符串。")
    reviewer: str = Field(min_length=1, max_length=200, description="注册中心审核员姓名或代号。")

    @field_validator("request_id", "target_platform_code", "reviewer")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("不能仅包含空白字符")
        return value


class AcknowledgeDeletionReviewRequest(BaseModel):
    model_config = {"extra": "forbid"}

    message_id: str = Field(min_length=1, max_length=128, description="当前已拉取并处理完成的删除审批结果消息标识。")


class PublishDeletionReviewResponse(BaseModel):
    message_id: str = Field(description="中间件生成的删除审批结果唯一标识，与原删除申请标识不同。")
    status: Literal["published"] = Field(default="published", description="RabbitMQ 已确认删除审批结果写入。")
    message: str = Field(default="删除审批结果提交成功", description="提交结果提示。")


@router.post("/publish", status_code=201, response_model=PublishDeletionReviewResponse,
             summary="提交删除审批结果",
             responses={403: {"description": "非注册中心或无发布权限"},
                        409: {"description": "同目标子平台、同 request_id 的结果尚未确认"},
                        422: {"description": "请求参数或目标平台无效"},
                        503: {"description": "去重不可用、发布未获确认或资源不可用"}})
async def publish_deletion_review(body: PublishDeletionReviewRequest, request: Request,
                                 platform: PlatformIdentity) -> PublishDeletionReviewResponse:
    try:
        message_id = await request.app.state.deletion_review.submit(
            platform.code, platform.access_token, **body.model_dump(),
        )
    except InvalidAccessTokenError:
        raise HTTPException(401, "令牌缺失、无效或已过期",
                            headers={"WWW-Authenticate": "Bearer"}) from None
    except PlatformPublishForbiddenError:
        raise HTTPException(403, "仅注册中心可以提交删除审批结果") from None
    except InvalidReviewTargetError:
        raise HTTPException(422, "target_platform_code 必须为配置中的子平台代号") from None
    except DuplicateStreamMessageError as error:
        raise HTTPException(409, {"message": "该子平台的 request_id 已有尚未确认的删除审批结果",
                                  "message_id": error.message_id, "offset": error.offset}) from None
    except StreamDeduplicationUnavailableError:
        raise HTTPException(503, "无法确认删除审批结果是否重复，请稍后重试或核对此前投递结果") from None
    except PlatformResourcesUnavailableError:
        raise HTTPException(503, "消息服务暂不可用，请稍后重试") from None
    except ReviewPublishError as error:
        raise HTTPException(503, {"message": "消息发布未获确认，需核对投递结果",
                                  "message_id": error.message_id}) from None
    return PublishDeletionReviewResponse(message_id=message_id)


class PullDeletionReviewResponse(BaseModel):
    offset: int = Field(description="删除审批结果在流中的位置，仅供观察，不接受客户端用其推进进度。")
    message: dict = Field(description="属于当前认证子平台的完整删除审批结果消息，包含 approved 等业务字段。")


class AcknowledgeDeletionReviewResponse(BaseModel):
    message_id: str = Field(description="已确认处理完成的删除审批结果消息标识。")
    status: Literal["acknowledged"] = Field(default="acknowledged", description="消费进度已写入 RabbitMQ 并回查确认。")
    offset: int = Field(description="本次确认的删除审批结果 offset。")


@asynccontextmanager
async def consumption_errors():
    try:
        yield
    except InvalidAccessTokenError:
        raise HTTPException(401, "令牌缺失、无效或已过期",
                            headers={"WWW-Authenticate": "Bearer"}) from None
    except PlatformConsumeForbiddenError:
        raise HTTPException(403, "仅子平台可以消费删除审批结果") from None
    except AcknowledgementConflictError:
        raise HTTPException(409, "消息不是当前待确认删除审批结果，请先拉取") from None
    except InvalidStreamMessageError:
        raise HTTPException(502, "流消息格式异常或超过缓存上限，未跳过异常消息") from None
    except (ConsumptionUnavailableError, PlatformResourcesUnavailableError):
        raise HTTPException(503, "消费服务暂不可用，请重试") from None


@router.post("/pull", response_model=PullDeletionReviewResponse, summary="拉取删除审批结果",
             responses={204: {"description": "本次扫描未发现本平台结果，请继续轮询"},
                        403: {"description": "无消费权限"}, 502: {"description": "消息格式异常或超过缓存上限"},
                        503: {"description": "消费服务不可用"}})
async def pull_deletion_review(request: Request, platform: PlatformIdentity, response: Response):
    async with consumption_errors():
        delivery = await request.app.state.deletion_review_consumer.pull(platform.code, platform.access_token)
    if delivery is None:
        return Response(status_code=204, headers={"Cache-Control": "no-store"})
    response.headers["Cache-Control"] = "no-store"
    return PullDeletionReviewResponse(offset=delivery.offset, message=delivery.message)


@router.post("/ack", response_model=AcknowledgeDeletionReviewResponse, summary="确认删除审批结果",
             responses={403: {"description": "无消费权限"}, 409: {"description": "不是当前待确认结果"},
                        503: {"description": "消费进度未获确认"}})
async def acknowledge_deletion_review(body: AcknowledgeDeletionReviewRequest, request: Request,
                                     platform: PlatformIdentity) -> AcknowledgeDeletionReviewResponse:
    async with consumption_errors():
        offset = await request.app.state.deletion_review_consumer.acknowledge(
            platform.code, platform.access_token, body.message_id,
        )
    return AcknowledgeDeletionReviewResponse(message_id=body.message_id, offset=offset)
