"""注册中心读取入库和删除审核附件。"""

from typing import Annotated
import anyio
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import StreamingResponse

from app.router.dependencies import AuthenticatedPlatform, require_platform
from app.service.resource import (
    FILE_ID_PATTERN, FILE_EXTENSION_PATTERN, OpenedResource,
    ResourceForbiddenError, ResourceNotFoundError, ResourceReadError,
)

router = APIRouter(prefix="/resource", tags=["审核附件"], responses={
    401: {"description": "身份缺失、无效或过期"},
    403: {"description": "非注册中心"},
    404: {"description": "文件不存在、已清理或不是可读取的普通文件"},
    503: {"description": "文件读取服务暂不可用"},
})
PlatformIdentity = Annotated[AuthenticatedPlatform, Depends(require_platform)]
FileId = Annotated[str, Path(pattern=FILE_ID_PATTERN, description="申请消息中的 file.file_id，不是 source_id 或 passport。")]
FileExtension = Annotated[str, Query(pattern=FILE_EXTENSION_PATTERN, description="申请消息中的 file.file_extension，不带点，读取时转为小写。")]


class ResourceResponse(StreamingResponse):
    def __init__(self, resource: OpenedResource):
        self._file = resource.file
        async def chunks():
            while chunk := await run_in_threadpool(self._file.read, 1024 * 1024):
                yield chunk
        super().__init__(chunks(), media_type="application/octet-stream", headers={
            "Content-Length": str(resource.size),
            "Content-Disposition": f'attachment; filename="{resource.filename}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        })

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            # 包括客户端中断和读取失败；避免大文件下载遗留打开的句柄。
            with anyio.CancelScope(shield=True):
                await run_in_threadpool(self._file.close)


async def read_resource(request: Request, platform: AuthenticatedPlatform, kind: str,
                        file_id: str, file_extension: str) -> ResourceResponse:
    try:
        resource = await request.app.state.resources.open(platform.code, kind, file_id, file_extension)
    except ResourceForbiddenError:
        raise HTTPException(403, "仅注册中心可以读取审核附件") from None
    except ResourceNotFoundError:
        raise HTTPException(404, "审核附件不存在或已清理") from None
    except ResourceReadError:
        raise HTTPException(503, "审核附件读取暂不可用") from None
    return ResourceResponse(resource)


@router.get("/ingestion-requests/{file_id}", response_class=ResourceResponse, status_code=200, summary="读取入库审核附件",
            responses={200: {"description": "原始文件二进制", "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}}})
async def read_ingestion_file(request: Request, file_id: FileId, file_extension: FileExtension,
                              platform: PlatformIdentity):
    return await read_resource(request, platform, "ingestion", file_id, file_extension)


@router.get("/deletion-requests/{file_id}", response_class=ResourceResponse, status_code=200, summary="读取删除审核附件",
            responses={200: {"description": "原始文件二进制", "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}}})
async def read_deletion_file(request: Request, file_id: FileId, file_extension: FileExtension,
                             platform: PlatformIdentity):
    return await read_resource(request, platform, "deletion", file_id, file_extension)
