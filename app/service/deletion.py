"""子平台提交删除申请业务信息，不保存附件或执行业务文件删除。"""

import asyncio
import json
from datetime import datetime, timezone
from uuid import uuid4
from app.service.passport import InvalidPassportError, normalize_passports
from app.service.platform_session import (
    InvalidAccessTokenError, PlatformPublishForbiddenError, PlatformResourcesUnavailableError,
)


class InvalidDeletionRequestError(ValueError):
    """删除申请业务字段不符合契约。"""


class DeletionPublishError(Exception):
    """消息未获发布确认，携带标识供调用方核对。"""

    def __init__(self, message_id: str):
        self.message_id = message_id


class DeletionService:
    def __init__(self, sessions, stream: str, registry_code: str, *, deduplicator):
        self._sessions = sessions
        self._stream = stream
        self._registry_code = registry_code
        self._deduplicator = deduplicator

    async def submit(self, platform_code: str, access_token: str, *, source_id: str,
                     passport: str | list[str], name: str, abstract: str, uploader: str, applicant: str,
                     note: str) -> str:
        if platform_code == self._registry_code:
            raise PlatformPublishForbiddenError()
        try:
            passport = normalize_passports(passport, required=True)
        except InvalidPassportError as error:
            raise InvalidDeletionRequestError(str(error)) from None
        fields = {"source_id": source_id, "passport": passport, "name": name, "abstract": abstract,
                  "uploader": uploader, "applicant": applicant, "note": note}
        # 服务层复查核心约束，防止非 HTTP 调用写入无效消息。
        limits = {"source_id": 128, "name": 200,
                  "abstract": 10000, "uploader": 200, "applicant": 200}
        for key, maximum in limits.items():
            value = fields[key]
            if not isinstance(value, str) or not value.strip() or (maximum is not None and len(value) > maximum):
                raise InvalidDeletionRequestError(key)
        if not isinstance(note, str) or len(note) > 10000:
            raise InvalidDeletionRequestError("note")
        await self._sessions.check_publish_access(platform_code, access_token, self._stream)
        # 只发送业务字段；去重仍使用平台及 source_id，并比较本流 registry 的确认进度。
        async with self._deduplicator.guard(platform_code, source_id):
            message_id = uuid4().hex
            message = {**fields, "message_id": message_id, "platform_code": platform_code,
                       "created_at": datetime.now(timezone.utc).isoformat()}
            try:
                await self._sessions.publish(
                    platform_code, access_token, self._stream,
                    json.dumps(message, ensure_ascii=False).encode("utf-8"),
                )
            except (InvalidAccessTokenError, PlatformPublishForbiddenError, PlatformResourcesUnavailableError):
                raise
            except asyncio.CancelledError:
                self._deduplicator.remember(platform_code, source_id, message_id, uncertain=True)
                raise
            except Exception:
                # 超时不等于未入流；保留业务键占位和消息 ID，后续核对，避免盲目重发。
                self._deduplicator.remember(platform_code, source_id, message_id, uncertain=True)
                raise DeletionPublishError(message_id) from None
            self._deduplicator.remember(platform_code, source_id, message_id)
            return message_id
