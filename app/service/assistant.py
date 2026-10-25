"""AI 助理任务类型、服务端元数据与发布幂等。"""

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field
from starlette.datastructures import UploadFile

from app.infrastructure.redis_assistant import (
    RedisAssistantTaskStore, AssistantRequestConflictError, AssistantQueueFullError,
)
from app.service.assistant_file import AssistantFileReference, AssistantFileStore


TaskType = Literal["reminder", "user_message", "assistant_message", "heartbeat"]
TASK_PRIORITIES = {"reminder": 0, "user_message": 1, "assistant_message": 2, "heartbeat": 3}


def validate_assistant_source(task_type: TaskType, source_assistant_id: str | None) -> None:
    """发布前校验助理来源；历史任务读取不强制补填或推断来源。"""
    if source_assistant_id is None:
        if task_type == "assistant_message":
            raise ValueError("assistant_message 必须提供 source_assistant_id")
    elif (not isinstance(source_assistant_id, str) or not source_assistant_id.strip()
          or len(source_assistant_id) > 128):
        raise ValueError("source_assistant_id 必须为非空白的 1–128 字符字符串")


class AssistantUserMessage(BaseModel):
    message_id: str = Field(description="该条原始用户消息的唯一标识，由中间件生成，合并后仍独立保留。")
    message: str = Field(description="本条用户消息的原始正文，完整保留 Markdown 和换行。")
    source_platform_code: str = Field(description="发布本条用户消息的平台代码，来自该次请求的平台认证。")
    source_assistant_id: str | None = Field(default=None, description="本条原始消息声明的发送方助理标识；无助理来源或旧消息未记录时为 null。")
    request_id: str | None = Field(description="本条用户消息的生产端幂等标识；未提供时为 null。")
    created_at: datetime = Field(description="本条用户消息成功接收时的 Redis 服务端 UTC 时间。")
    files: list[AssistantFileReference] = Field(default_factory=list, description="本条原始用户消息的附件引用，合并后仍与本条消息绑定；无附件为空列表。")


class AssistantTask(BaseModel):
    task_id: str = Field(description="中间件生成的全局唯一任务标识。")
    userid: str = Field(description="接收该任务的用户唯一标识。")
    task_type: TaskType = Field(description="任务类型，优先顺序为 reminder、user_message、assistant_message、heartbeat；heartbeat 用于助理空闲时的周期唤醒。")
    message: str = Field(description="任务消息；单条保持原文，多条用户消息按接收顺序仅以空行和 Markdown 分隔线拼接，不添加标题或时间。")
    source_platform_code: str = Field(description="创建任务时从有效平台令牌解析出的来源代码；合并任务各条消息的来源见 user_messages。")
    source_assistant_id: str | None = Field(default=None, description="发送方助理的唯一标识，由生产平台提供；合并用户任务表示首条来源，各条来源见 user_messages；未记录时为 null。")
    request_id: str | None = Field(description="创建任务的首条消息的生产端幂等标识；未提供时为 null，各合并消息的标识见 user_messages。")
    created_at: datetime = Field(description="Redis 服务端 UTC 创建时间；合并用户任务采用组内最晚一条原始消息的接收时间，其他任务采用初次入队时间。")
    sequence: str = Field(description="用户任务队列的递增十进制序号，同优先级按此序号先进先出；字符串避免客户端整数精度丢失。")
    user_messages: list[AssistantUserMessage] = Field(default_factory=list, description="用户任务的原始消息列表，按接收顺序保存；其他类型为空列表，领取后列表不再变化。")
    files: list[AssistantFileReference] = Field(default_factory=list, description="整个任务的附件引用汇总；合并用户任务中每个文件的归属见 user_messages[].files。")


class PublishAssistantTaskResponse(BaseModel):
    status: Literal["published"] = Field(default="published", description="Redis 已返回任务入队成功或相同幂等请求已成功写入。")
    duplicate: bool = Field(description="是否为相同 request_id 和相同内容的重试；为 true 时返回原任务，不重复入队。")
    merged: bool = Field(default=False, description="本次原始发布是否追加到了已有待领取用户任务；幂等重试保留该次原始发布的标记。")
    task: AssistantTask = Field(description="成功写入的原始任务及中间件元数据；完成后的幂等保留期内仍返回原任务，不重复入队。")


class PullAssistantTaskResponse(BaseModel):
    task: AssistantTask = Field(description="本次交付的任务；同平台在有效租约内重复领取返回同一任务。")
    delivery_token: str = Field(repr=False, description="本次交付凭证，确认时原样提交；超时重领后旧凭证失效。")
    lease_expires_at: datetime = Field(description="任务执行租约的 UTC 截止时间；到期前同平台重复 pull 可续租。")


class AcknowledgeAssistantTaskResponse(BaseModel):
    task_id: str = Field(description="已确认处理完成并从任务存储移除的任务唯一标识。")
    status: Literal["acknowledged"] = Field(default="acknowledged", description="调用平台声明任务业务处理已完成，Redis 已确认清除执行记录和正文；中间件不核验外部结果交付。")
    duplicate: bool = Field(description="是否为幂等保留期内对同一任务、同一交付凭证的重复确认。")


class AssistantTaskStatusResponse(BaseModel):
    userid: str = Field(description="查询状态的用户唯一标识。")
    has_in_progress_task: bool = Field(description="是否存在执行租约仍有效的已领取任务；租约过期时为 false，不代表旧执行者已停止。")
    task_id: str | None = Field(description="当前有效执行租约对应的任务 ID；无有效执行中任务时为 null。")
    task_type: TaskType | None = Field(description="当前有效执行中任务的类型；无有效执行中任务时为 null。")
    lease_expires_at: datetime | None = Field(description="当前任务执行租约的 UTC 截止时间；无有效执行中任务时为 null。")
    last_heartbeat_at: datetime | None = Field(description="最近一次成功确认 heartbeat 任务的 Redis 服务端 UTC 时间；未确认过心跳或升级前无记录时为 null。")


class AssistantTaskService:
    def __init__(self, store: RedisAssistantTaskStore, files: AssistantFileStore | None = None):
        self._store = store
        self.files = files if files is not None else AssistantFileStore()

    async def publish(self, source_platform_code: str, *, userid: str, task_type: TaskType,
                      message: str, request_id: str | None = None,
                      source_assistant_id: str | None = None,
                      uploads: list[UploadFile] | None = None,
                      file_extensions: list[str] | None = None) -> PublishAssistantTaskResponse:
        # 必须先校验来源，再落盘附件，防止绕过 HTTP 入口产生无来源助理任务。
        validate_assistant_source(task_type, source_assistant_id)
        saved_files = await self.files.save(uploads or [], file_extensions or [])
        # 指纹采用后缀及文件内容，不采用每次上传新生成的文件 ID 或客户端文件名。
        identity = [task_type, message]
        if saved_files:
            identity.append([[item.reference.file_extension, item.sha256] for item in saved_files])
        # 未提供助理来源时沿用历史指纹；提供后来源变化也视为不同发布内容。
        if source_assistant_id is not None:
            identity.append({'source_assistant_id': source_assistant_id})
        content = json.dumps(identity, ensure_ascii=False, separators=(",", ":"))
        fingerprint = hashlib.sha256(content.encode()).hexdigest()
        # JSON 数组编码避免平台代码与请求 ID 拼接时产生边界歧义。
        key = json.dumps([source_platform_code, request_id], separators=(",", ":")) if request_id else ""
        payload = dict(task_id=uuid4().hex, userid=userid, task_type=task_type, message=message,
                       source_platform_code=source_platform_code, request_id=request_id,
                       source_assistant_id=source_assistant_id)
        references = [item.reference for item in saved_files]
        if references:
            payload['files'] = [item.model_dump() for item in references]
        try:
            created, merged, encoded = await self._store.publish(
                userid, idempotency_key=key, fingerprint=fingerprint, task_id=payload["task_id"],
                priority=TASK_PRIORITIES[task_type], payload=json.dumps(payload, ensure_ascii=False),
            )
        except (AssistantRequestConflictError, AssistantQueueFullError):
            await self.files.delete(references)
            raise
        # 结果不确定时保留文件，避免已入队任务引用被删；已确认的幂等重试只回收本次多余上传。
        if not created:
            await self.files.delete(references)
        return PublishAssistantTaskResponse(duplicate=not created, merged=merged, task=self._decode(encoded))

    @staticmethod
    def _timestamp(raw: str) -> datetime:
        # 使用 Decimal 拆分秒和微秒，避免浮点时间转换造成一微秒偏差。
        epoch = Decimal(raw)
        seconds = int(epoch)
        return datetime.fromtimestamp(seconds, timezone.utc).replace(microsecond=int((epoch - seconds) * 1000000))

    @staticmethod
    def _decode(encoded: str) -> AssistantTask:
        saved = json.loads(encoded)
        saved.pop("_request_key", None)
        saved["created_at"] = AssistantTaskService._timestamp(saved.pop("created_at_unix"))
        fragments = []
        for item in saved.get("user_messages", []):
            item["created_at"] = AssistantTaskService._timestamp(item.pop("created_at_unix"))
            fragments.append(AssistantUserMessage(**item))
        # 兼容功能上线前的用户任务，响应中也补全单条原始消息信息。
        if saved["task_type"] == "user_message" and not fragments:
            fragments = [AssistantUserMessage(
                message_id=saved["task_id"], message=saved["message"], created_at=saved["created_at"],
                source_platform_code=saved["source_platform_code"], request_id=saved["request_id"],
                source_assistant_id=saved.get('source_assistant_id'),
                files=saved.get('files', []),
            )]
        saved["user_messages"] = fragments
        if fragments:
            # 同时兼容旧版本已合并的任务，不能继续把首条时间当作聚合任务的创建时间。
            saved["created_at"] = max(item.created_at for item in fragments)
        if len(fragments) > 1:
            saved["message"] = "\n\n---\n\n".join(item.message for item in fragments)
        return AssistantTask(**saved)

    async def pull(self, platform_code: str, userid: str) -> PullAssistantTaskResponse | None:
        delivery = await self._store.pull(userid, platform_code, uuid4().hex)
        if delivery is None:
            return None
        encoded, token, deadline = delivery
        seconds, milliseconds = divmod(deadline, 1000)
        expires_at = datetime.fromtimestamp(seconds, timezone.utc).replace(microsecond=milliseconds * 1000)
        return PullAssistantTaskResponse(task=self._decode(encoded), delivery_token=token,
                                         lease_expires_at=expires_at)

    async def acknowledge(self, platform_code: str, *, userid: str, task_id: str,
                          delivery_token: str) -> AcknowledgeAssistantTaskResponse:
        # 业务动作和结果交付由调用平台在确认前完成；中间件只校验交付凭证并清理原任务。
        duplicate, files = await self._store.acknowledge(userid, platform_code, task_id, delivery_token)
        # Redis 回执保留文件清单，清理失败后可重试同一 ack；不能在确认之前删除文件。
        await self.files.delete([AssistantFileReference(**item) for item in files])
        return AcknowledgeAssistantTaskResponse(task_id=task_id, duplicate=duplicate)

    async def status(self, userid: str) -> AssistantTaskStatusResponse:
        task_id, task_type, deadline, heartbeat = await self._store.status(userid)
        expires_at = None
        if task_id:
            seconds, milliseconds = divmod(deadline, 1000)
            expires_at = datetime.fromtimestamp(seconds, timezone.utc).replace(microsecond=milliseconds * 1000)
        last_heartbeat_at = None
        if heartbeat:
            epoch = Decimal(heartbeat)
            seconds = int(epoch)
            last_heartbeat_at = datetime.fromtimestamp(seconds, timezone.utc).replace(
                microsecond=int((epoch - seconds) * 1000000),
            )
        return AssistantTaskStatusResponse(
            userid=userid, has_in_progress_task=bool(task_id), task_id=task_id or None,
            task_type=task_type or None, lease_expires_at=expires_at,
            last_heartbeat_at=last_heartbeat_at,
        )
