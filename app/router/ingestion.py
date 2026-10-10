"""入库请求上传接口。"""

from contextlib import asynccontextmanager
from fastapi.responses import JSONResponse
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, Field

from app.router.dependencies import AuthenticatedPlatform, require_platform
from app.service.ingestion import (
    FILE_EXTENSION_PATTERN, FileStorageError, MessagePublishError, InvalidFileExtensionError, InvalidSourceIdError,
)
from app.service.platform_session import (
    PlatformConsumeForbiddenError, InvalidAccessTokenError, PlatformPublishForbiddenError, PlatformResourcesUnavailableError,
)
from app.service.ingestion_deduplication import DuplicateIngestionError, IngestionDeduplicationUnavailableError

from app.service.ingestion_consumer import (
    AcknowledgedFileCleanupError, AcknowledgementConflictError, ConsumptionUnavailableError, InvalidStreamMessageError,
)


@asynccontextmanager
async def consumption_errors():
    try:
        yield
    except InvalidAccessTokenError:
        raise HTTPException(401, "令牌缺失、无效或已过期", headers={"WWW-Authenticate": "Bearer"}) from None
    except PlatformConsumeForbiddenError:
        raise HTTPException(403, "该平台无权消费入库请求") from None
    except AcknowledgementConflictError:
        raise HTTPException(409, "消息不是当前待确认消息，请先拉取") from None
    except AcknowledgedFileCleanupError:
        raise HTTPException(503, "消费进度已确认，但文件清理失败，请重试同一 ack") from None
    except InvalidStreamMessageError:
        raise HTTPException(502, "流消息格式异常，未推进消费进度") from None
    except (ConsumptionUnavailableError, PlatformResourcesUnavailableError):
        raise HTTPException(503, "消费服务暂不可用，请重试") from None


router = APIRouter(prefix="/ingestion-requests", tags=["入库请求"])


class IngestionResponse(BaseModel):
    message_id: str = Field(description="入库请求唯一标识，同时作为文件标识。")
    status: Literal["published"] = Field(default="published", description="文件已保存且 RabbitMQ 已确认消息写入。")
    message: str = Field(default="入库请求提交成功", description="提交结果提示。")


@router.post("/publish", response_model=IngestionResponse, status_code=201, summary="提交入库请求",
             responses={401: {"description": "身份无效"}, 403: {"description": "无生产权限"},
                        409: {"description": "同平台、同 source_id 的申请尚未确认"},
                        500: {"description": "文件保存失败"}, 503: {"description": "去重不可用、发布未确认或资源不可用"}})
async def submit_ingestion(
    request: Request,
    source_id: Annotated[str, Form(min_length=1, max_length=128, description="数据在子平台内的唯一 ID；与令牌识别的平台代码组成去重键，已有未确认申请时拒绝再次提交。")],
    name: Annotated[str, Form(min_length=1, max_length=200, description="入库资料名称。")],
    abstract: Annotated[str, Form(min_length=1, max_length=10000, description="入库资料摘要。")],
    reviewer: Annotated[str, Form(min_length=1, max_length=200, description="发起请求的平台审核员姓名或代号。")],
    file_extension: Annotated[str, Form(pattern=FILE_EXTENSION_PATTERN, description="文件后缀，不带点，1–16 位英文字母或数字；保存为小写，例如 pdf。")],
    file: Annotated[UploadFile, File(description="需要转存的单个文件。")],
    platform: Annotated[AuthenticatedPlatform, Depends(require_platform)],
    # 空表单字符串会被 FastAPI 按缺省值处理；默认空字符串表示没有沟通备注。
    note: Annotated[str, Form(max_length=10000, description="平台与注册中心审核员的沟通说明，允许为空字符串；省略时默认为空字符串。")] = "",
) -> IngestionResponse:
    for field, value in (("name", name), ("abstract", abstract), ("source_id", source_id)):
        if not value.strip():
            raise HTTPException(422, f"{field} 不能仅包含空白字符")
    if not reviewer.strip():
        raise HTTPException(422, "reviewer 不能仅包含空白字符")
    try:
        message_id = await request.app.state.ingestion.submit(
            platform.code, platform.access_token, note, reviewer, file, file_extension, name=name, abstract=abstract, source_id=source_id,
        )
    except InvalidSourceIdError:
        raise HTTPException(422, "source_id 必须为非空白的 1–128 字符字符串") from None
    except InvalidFileExtensionError:
        raise HTTPException(422, "file_extension 必须为不带点的 1–16 位英文字母或数字") from None
    except InvalidAccessTokenError:
        raise HTTPException(401, "令牌缺失、无效、已过期或与平台不匹配",
                            headers={"WWW-Authenticate": "Bearer"}) from None
    except PlatformPublishForbiddenError:
        raise HTTPException(403, "该平台无权提交入库请求") from None
    except DuplicateIngestionError as error:
        raise HTTPException(409, {"message": "该平台的 source_id 已有尚未确认的入库请求",
                                  "message_id": error.message_id, "offset": error.offset}) from None
    except IngestionDeduplicationUnavailableError:
        raise HTTPException(503, "无法确认入库请求是否重复，请稍后重试或核对此前投递结果") from None
    except PlatformResourcesUnavailableError:
        raise HTTPException(503, "消息服务暂不可用，请稍后重试") from None
    except FileStorageError:
        raise HTTPException(500, "文件保存失败") from None
    except MessagePublishError as error:
        raise HTTPException(503, {"message": "消息发布未获确认，文件已保留，需核对投递结果",
                                  "message_id": error.message_id}) from None
    return IngestionResponse(message_id=message_id)


class PullResponse(BaseModel):
    offset: int = Field(description="消息在入库流中的位置，仅供观察，不接受客户端用其推进消费进度。")
    message: dict = Field(description="入库消息完整 JSON 对象，包含 message_id、资料信息及文件引用。")


class AcknowledgeRequest(BaseModel):
    model_config = {"extra": "forbid"}
    message_id: str = Field(min_length=1, max_length=128, description="当前已拉取并处理成功的消息标识。")


class AcknowledgeResponse(BaseModel):
    message_id: str = Field(description="已确认完成的消息标识。")
    status: Literal["acknowledged"] = Field(default="acknowledged", description="对应消费进度已经写入并从 RabbitMQ 回查确认。")
    offset: int = Field(description="本次消息对应的已确认 offset。")


@router.post("/pull", response_model=PullResponse, summary="拉取一条入库请求",
             responses={204: {"description": "暂时没有新消息"}, 401: {"description": "身份无效"},
                        403: {"description": "无消费权限"}, 502: {"description": "消息格式异常"},
                        503: {"description": "消费服务不可用"}})
async def pull_ingestion(
    request: Request,
    platform: Annotated[AuthenticatedPlatform, Depends(require_platform)],
):
    async with consumption_errors():
        delivery = await request.app.state.ingestion_consumer.pull(platform.code, platform.access_token)
    if delivery is None:
        return Response(status_code=204, headers={"Cache-Control": "no-store"})
    return JSONResponse(content=PullResponse(offset=delivery.offset, message=delivery.message).model_dump(),
                        headers={"Cache-Control": "no-store"})


@router.post("/ack", response_model=AcknowledgeResponse, summary="确认入库请求处理完成",
             responses={401: {"description": "身份无效"}, 403: {"description": "无消费权限"},
                        409: {"description": "不是当前待确认消息"}, 502: {"description": "消息文件引用非法"}, 503: {"description": "消费进度未获确认"}})
async def acknowledge_ingestion(
    body: AcknowledgeRequest,
    request: Request,
    platform: Annotated[AuthenticatedPlatform, Depends(require_platform)],
) -> AcknowledgeResponse:
    async with consumption_errors():
        offset = await request.app.state.ingestion_consumer.acknowledge(
            platform.code, platform.access_token, body.message_id,
        )
    return AcknowledgeResponse(message_id=body.message_id, offset=offset)
