"""AI 个人助理任务发布、领取和确认入口。"""

from typing import Annotated
from contextlib import asynccontextmanager
import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.router.dependencies import AuthenticatedPlatform, require_platform
from app.service.assistant import (
    PublishAssistantTaskResponse, PullAssistantTaskResponse,
    AcknowledgeAssistantTaskResponse, TaskType,
    AssistantTaskStatusResponse,
    validate_assistant_source,
)
from app.infrastructure.redis_assistant import (
    AssistantQueueFullError, AssistantRequestConflictError,
    AssistantTaskBusyError, AssistantAcknowledgementConflictError,
)
from app.service.assistant_file import AssistantFileStorageError, AssistantFileTooLargeError
from app.service.file_message import FILE_EXTENSION_PATTERN


router = APIRouter(
    prefix="/assistant/tasks", tags=["助理任务"],
    responses={401: {"description": "平台令牌缺失、无效或已过期"}},
)
PlatformIdentity = Annotated[AuthenticatedPlatform, Depends(require_platform)]


@router.get("/status", response_model=AssistantTaskStatusResponse, summary="查询助理任务状态",
            description="只读查询有效执行中任务及最近一次成功确认心跳的时间；不领取任务、不续租、不生成心跳。",
            responses={503: {"description": "任务存储不可用"}})
async def get_assistant_task_status(
    request: Request, response: Response, platform: PlatformIdentity,
    userid: Annotated[str, Query(min_length=1, max_length=128, pattern=r"\S",
                                description="查询状态的用户唯一标识，不能仅包含空白字符。")],
) -> AssistantTaskStatusResponse:
    result = await request.app.state.assistant_tasks.status(userid)
    response.headers["Cache-Control"] = "no-store"
    return result


class AssistantUserRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    userid: str = Field(min_length=1, max_length=128, description="接收或处理任务的用户唯一标识，用于定位该用户的个人助理任务队列。")

    @field_validator("userid")
    @classmethod
    def reject_blank_userid(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("用户标识不能仅包含空白字符")
        return value


class PublishAssistantTaskRequest(AssistantUserRequest):
    model_config = ConfigDict(extra="forbid", json_schema_extra={
        'if': {'properties': {'task_type': {'const': 'assistant_message'}}, 'required': ['task_type']},
        'then': {'required': ['source_assistant_id'], 'properties': {'source_assistant_id': {
            'type': 'string', 'minLength': 1, 'maxLength': 128, 'pattern': r'\S',
            'description': 'assistant_message 必须提供非空白的发送方助理唯一标识。',
        }}},
    })
    task_type: TaskType = Field(description="任务类型及优先级，从高到低为 reminder（提醒）、user_message（用户发给自己助理的消息）、assistant_message（其他助理发来的消息）、heartbeat（助理保活心跳，由生产端定期发布）。")
    message: str = Field(min_length=1, max_length=10000, description="本条任务的具体消息，允许 Markdown 文本，不接受空字符串或纯空白；user_message 仅在与上一条消息的接收时间间隔未超限、任务尚未领取且未达到合并容量上限时追加。")
    request_id: str | None = Field(default=None, min_length=1, max_length=128, description="生产端可选的幂等标识；同来源平台、同用户下用于识别重试请求。")
    source_assistant_id: str | None = Field(default=None, min_length=1, max_length=128, description="发送方助理的唯一标识，由生产平台填写，不是接收方 userid 或认证平台代码；assistant_message 必填且不能为 null 或空白，其他类型可省略或为 null。")

    @field_validator("message", "request_id", "source_assistant_id")
    @classmethod
    def reject_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("不能仅包含空白字符")
        return value

    @model_validator(mode="after")
    def require_assistant_source(self):
        validate_assistant_source(self.task_type, self.source_assistant_id)
        return self


class AcknowledgeAssistantTaskRequest(AssistantUserRequest):
    task_id: str = Field(min_length=1, max_length=128, description="已领取并完成处理的任务唯一标识，由中间件生成。")
    delivery_token: str = Field(min_length=1, max_length=128, repr=False, description="领取任务时返回的本次交付凭证，用于拒绝过期或不匹配的确认。")

    @field_validator("task_id", "delivery_token")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("不能仅包含空白字符")
        return value


@asynccontextmanager
async def read_publish_body(request: Request):
    form = None
    try:
        media_type = request.headers.get('content-type', '').split(';', 1)[0].strip().lower()
        uploads, extensions = [], []
        if media_type == 'application/json':
            try:
                data = await request.json()
            except (ValueError, UnicodeDecodeError):
                raise HTTPException(422, '请求体必须为有效 JSON') from None
        elif media_type == 'multipart/form-data':
            try:
                form = await request.form(max_files=request.app.state.assistant_tasks.files.max_files)
            except StarletteHTTPException as error:
                raise HTTPException(422, error.detail) from None
            data = {}
            for key, value in form.multi_items():
                if key == 'files':
                    if not isinstance(value, UploadFile):
                        raise HTTPException(422, 'files 必须为文件字段')
                    uploads.append(value)
                elif key == 'file_extensions':
                    if not isinstance(value, str):
                        raise HTTPException(422, 'file_extensions 必须为字符串字段')
                    extensions.append(value)
                else:
                    if key in data or not isinstance(value, str):
                        raise HTTPException(422, '普通表单字段不能重复或为文件')
                    data[key] = value
        else:
            raise HTTPException(415, '只支持 application/json 或 multipart/form-data')
        try:
            body = PublishAssistantTaskRequest.model_validate(data)
        except ValidationError as error:
            errors = [{**item, 'loc': ('body', *item['loc'])} for item in error.errors()]
            raise RequestValidationError(errors) from None
        yield body, uploads, extensions
    finally:
        if form is not None:
            await form.close()


# 同一 publish 路径兼容原有 JSON 与带附件表单；显式声明两种机器可读请求契约。
_json_schema = PublishAssistantTaskRequest.model_json_schema()
_multipart_schema = {
    **_json_schema,
    'properties': {**_json_schema['properties'],
                   'files': {'type': 'array', 'description': '本条消息的多个附件，按顺序与 file_extensions 一一对应。',
                             'items': {'type': 'string', 'format': 'binary'}},
                   'file_extensions': {'type': 'array', 'description': '各附件的显式后缀，不带点，顺序与 files 对应。',
                                       'items': {'type': 'string', 'pattern': FILE_EXTENSION_PATTERN}}},
}


@router.post("/publish", status_code=201, response_model=PublishAssistantTaskResponse,
             openapi_extra={'requestBody': {'required': True, 'content': {
                 'application/json': {'schema': _json_schema},
                 'multipart/form-data': {'schema': _multipart_schema},
             }}},
             summary="发布助理任务", responses={
                 409: {"description": "同一幂等标识已用于不同任务内容"},
                 429: {"description": "该用户的任务队列已达到容量上限"},
                 413: {"description": "单个附件超过大小上限"},
                 415: {"description": "请求媒体类型不支持"},
                 422: {"description": "字段、文件数量或后缀不合法"},
                 503: {"description": "任务存储不可用或写入结果不确定"},
             })
async def publish_assistant_task(request: Request,
                                 response: Response, platform: PlatformIdentity) -> PublishAssistantTaskResponse:
    try:
        async with read_publish_body(request) as (body, uploads, extensions):
            result = await request.app.state.assistant_tasks.publish(
                platform.code, **body.model_dump(), uploads=uploads, file_extensions=extensions,
            )
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    except AssistantFileTooLargeError:
        raise HTTPException(413, '单个附件超过大小上限') from None
    except AssistantFileStorageError:
        raise HTTPException(503, '任务附件保存或多余上传清理失败，请使用相同 request_id 重试') from None
    except AssistantRequestConflictError:
        raise HTTPException(409, "同一平台、同一用户的 request_id 已用于不同任务内容") from None
    except AssistantQueueFullError:
        raise HTTPException(429, "该用户的任务队列已达到容量上限") from None
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/pull", response_model=PullAssistantTaskResponse, summary="领取助理任务",
             responses={204: {"description": "用户任务队列为空"},
                        409: {"description": "另一个平台正在处理该用户的任务"},
                        503: {"description": "任务存储不可用或领取结果不确定"}})
async def pull_assistant_task(body: AssistantUserRequest, request: Request,
                              response: Response, platform: PlatformIdentity):
    try:
        delivery = await request.app.state.assistant_tasks.pull(platform.code, body.userid)
    except AssistantTaskBusyError:
        raise HTTPException(409, "该用户已有另一个平台领取的执行中任务") from None
    if delivery is None:
        return Response(status_code=204, headers={"Cache-Control": "no-store"})
    response.headers["Cache-Control"] = "no-store"
    return delivery


@router.post("/ack", response_model=AcknowledgeAssistantTaskResponse, summary="确认助理任务",
             description="调用平台应先完成任务及结果交付，再确认原任务。给其他助理发消息时先向目标用户任务队列发布；给用户发消息时由平台完成发送或可靠交接。本接口不接收业务结果，只确认并清理原任务。",
             responses={409: {"description": "任务、领取平台、交付凭证不匹配或租约已过期"},
                        503: {"description": "任务存储不可用或确认结果不确定"}})
async def acknowledge_assistant_task(body: AcknowledgeAssistantTaskRequest, request: Request,
                                     response: Response, platform: PlatformIdentity) -> AcknowledgeAssistantTaskResponse:
    try:
        result = await request.app.state.assistant_tasks.acknowledge(platform.code, **body.model_dump())
    except AssistantAcknowledgementConflictError:
        raise HTTPException(409, "任务、领取平台、交付凭证不匹配，或执行租约已过期") from None
    except AssistantFileStorageError:
        raise HTTPException(503, '任务已确认但附件清理失败，请重试相同 ack') from None
    response.headers["Cache-Control"] = "no-store"
    return result
