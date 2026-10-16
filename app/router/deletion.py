"""删除申请发布及注册中心消费接口。"""

from contextlib import asynccontextmanager
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.router.dependencies import AuthenticatedPlatform, require_platform
from app.service.file_request_consumer import AcknowledgedFileCleanupError
from app.service.stream_consumer import (
    AcknowledgementConflictError, ConsumptionUnavailableError, InvalidStreamMessageError,
)
from app.service.deletion import DeletionPublishError, InvalidDeletionRequestError
from app.service.stream_deduplication import DuplicateStreamMessageError, StreamDeduplicationUnavailableError
from app.service.platform_session import (
    InvalidAccessTokenError, PlatformPublishForbiddenError, PlatformResourcesUnavailableError,
    PlatformConsumeForbiddenError,
)


router = APIRouter(
    prefix="/deletion-requests", tags=["删除申请"],
    responses={401: {"description": "令牌缺失、无效或已过期"}},
)
PlatformIdentity = Annotated[AuthenticatedPlatform, Depends(require_platform)]


class AcknowledgeDeletionRequest(BaseModel):
    model_config = {"extra": "forbid"}

    message_id: str = Field(min_length=1, max_length=128, description="当前已拉取并处理完成的删除申请消息标识。")


class PublishDeletionResponse(BaseModel):
    message_id: str = Field(description="中间件生成的删除申请唯一标识。")
    status: Literal["published"] = Field(default="published", description="RabbitMQ 已确认删除申请消息写入。")
    message: str = Field(default="删除申请提交成功", description="提交结果提示。")


@router.post("/publish", status_code=201, response_model=PublishDeletionResponse, summary="提交删除申请",
             responses={403: {"description": "注册中心或无发布权限"},
                        409: {"description": "同平台、同 source_id 的删除申请尚未确认"},
                        503: {"description": "去重不可用、发布未获确认或资源不可用"}})
async def publish_deletion(
    request: Request,
    source_id: Annotated[str, Form(min_length=1, max_length=128, description="待删除文件在子平台内的唯一 ID，与认证平台代码组成去重键；已有未确认删除申请时拒绝再次提交。")],
    passport: Annotated[list[str], Form(min_length=1, description="待删除数据的通行证列表，至少一项且每项非空白；多个值通过重复 passport 表单字段提交，单个字段兼容原有调用。")],
    name: Annotated[str, Form(min_length=1, max_length=200, description="待删除文件的业务名称，不作为磁盘路径。")],
    abstract: Annotated[str, Form(min_length=1, max_length=10000, description="待删除文件的摘要。")],
    uploader: Annotated[str, Form(min_length=1, max_length=200, description="原文件上传人的姓名或代号。")],
    applicant: Annotated[str, Form(min_length=1, max_length=200, description="本次删除申请人的姓名或代号，与原上传人分别记录。")],
    platform: PlatformIdentity,
    # FastAPI 将空表单字符串按缺省值处理；以空字符串默认值保留无备注的业务语义。
    note: Annotated[str, Form(max_length=10000, description="删除申请人的备注或删除原因，允许为空字符串；省略时默认为空字符串。")] = "",
) -> PublishDeletionResponse:
    # 契约已取消附件，拒绝旧文件字段，避免调用方误以为文件已被转存。
    form = await request.form()
    if "file" in form or "file_extension" in form:
        raise HTTPException(422, "删除申请不再接收 file 或 file_extension，请仅提交业务字段")
    # 上传人和申请人是业务主体；调用平台身份仍只从认证会话取得。
    for field, value in (("source_id", source_id), ("name", name),
                         ("abstract", abstract), ("uploader", uploader), ("applicant", applicant)):
        if not value.strip():
            raise HTTPException(422, f"{field} 不能仅包含空白字符")
    try:
        message_id = await request.app.state.deletion.submit(
            platform.code, platform.access_token,
            source_id=source_id, passport=passport, name=name, abstract=abstract,
            uploader=uploader, applicant=applicant, note=note,
        )
    except InvalidAccessTokenError:
        raise HTTPException(401, "令牌缺失、无效或已过期",
                            headers={"WWW-Authenticate": "Bearer"}) from None
    except PlatformPublishForbiddenError:
        raise HTTPException(403, "仅子平台可以提交删除申请") from None
    except DuplicateStreamMessageError as error:
        raise HTTPException(409, {"message": "该平台的 source_id 已有尚未确认的删除申请",
                                  "message_id": error.message_id, "offset": error.offset}) from None
    except StreamDeduplicationUnavailableError:
        raise HTTPException(503, "无法确认删除申请是否重复，请稍后重试或核对此前投递结果") from None
    except InvalidDeletionRequestError as error:
        raise HTTPException(422, f"删除申请字段不合法：{error}") from None
    except PlatformResourcesUnavailableError:
        raise HTTPException(503, "消息服务暂不可用，请稍后重试") from None
    except DeletionPublishError as error:
        raise HTTPException(503, {"message": "消息发布未获确认，需核对投递结果",
                                  "message_id": error.message_id}) from None
    return PublishDeletionResponse(message_id=message_id)


class PullDeletionResponse(BaseModel):
    offset: int = Field(description="删除申请在流中的位置，仅供观察，不接受客户端用其推进进度。")
    message: dict = Field(description="完整删除申请消息，包含 source_id、通行证字符串数组 passport 和人员信息；历史单通行证规范为数组，新消息不含附件引用。")


class AcknowledgeDeletionResponse(BaseModel):
    message_id: str = Field(description="已确认处理完成的删除申请消息标识。")
    status: Literal["acknowledged"] = Field(default="acknowledged", description="消费进度已回查确认；历史消息含附件时同时完成兼容清理。")
    offset: int = Field(description="已确认的删除申请 offset。")


@asynccontextmanager
async def consumption_errors():
    try:
        yield
    except InvalidAccessTokenError:
        raise HTTPException(401, "令牌缺失、无效或已过期",
                            headers={"WWW-Authenticate": "Bearer"}) from None
    except PlatformConsumeForbiddenError:
        raise HTTPException(403, "仅注册中心可以消费删除申请") from None
    except AcknowledgementConflictError:
        raise HTTPException(409, "消息不是当前待确认删除申请，请先拉取") from None
    except AcknowledgedFileCleanupError:
        raise HTTPException(503, "消费进度已确认，但文件清理失败，请重试同一 ack") from None
    except InvalidStreamMessageError:
        raise HTTPException(502, "流消息格式或历史附件引用异常，未推进消费进度") from None
    except (ConsumptionUnavailableError, PlatformResourcesUnavailableError):
        raise HTTPException(503, "消费服务暂不可用，请重试") from None


@router.post("/pull", response_model=PullDeletionResponse, summary="拉取删除申请",
             responses={204: {"description": "暂时没有新消息"}, 403: {"description": "非注册中心或无消费权限"},
                        502: {"description": "消息格式异常或超过缓存上限"},
                        503: {"description": "消费服务不可用"}})
async def pull_deletion(request: Request, response: Response, platform: PlatformIdentity):
    async with consumption_errors():
        delivery = await request.app.state.deletion_consumer.pull(platform.code, platform.access_token)
    if delivery is None:
        return Response(status_code=204, headers={"Cache-Control": "no-store"})
    response.headers["Cache-Control"] = "no-store"
    return PullDeletionResponse(offset=delivery.offset, message=delivery.message)


@router.post("/ack", response_model=AcknowledgeDeletionResponse, summary="确认删除申请处理完成",
             responses={403: {"description": "非注册中心或无消费权限"},
                        409: {"description": "不是当前待确认删除申请"},
                        502: {"description": "历史附件引用非法"},
                        503: {"description": "进度未获确认或历史附件清理失败"}})
async def acknowledge_deletion(body: AcknowledgeDeletionRequest, request: Request,
                               platform: PlatformIdentity) -> AcknowledgeDeletionResponse:
    async with consumption_errors():
        offset = await request.app.state.deletion_consumer.acknowledge(
            platform.code, platform.access_token, body.message_id,
        )
    return AcknowledgeDeletionResponse(message_id=body.message_id, offset=offset)
