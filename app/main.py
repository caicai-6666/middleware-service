"""FastAPI 应用装配与本地启动入口。"""

import asyncio
from contextlib import asynccontextmanager, suppress
from types import MappingProxyType

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError

from app.config.consumer import load_consumer_cache_settings
from app.config.assistant import load_assistant_task_settings
from app.config.platform import load_platforms_from_env
from app.config.stream import load_stream_settings
from app.service.ingestion_consumer import IngestionConsumerService
from app.service.ingestion import IngestionService
from app.service.ingestion_deduplication import IngestionDeduplicator
from app.service.stream_deduplication import StreamPublishDeduplicator
from app.service.deletion import DeletionService
from app.service.deletion_review import DeletionReviewService
from app.service.file_permission import FilePermissionService
from app.service.resource import ResourceService
from app.service.deletion_consumer import DeletionConsumerService
from app.service.review import ReviewService
from app.service.review_consumer import ReviewConsumerService
from app.service.platform_stream import PlatformStreamManager
from app.service.stream import verify_streams
from app.config.session import load_session_settings
from app.config.user import load_user_cache_settings
from app.config.redis import load_redis_settings
from app.infrastructure.redis import create_redis_client
from app.infrastructure.redis_user import RedisUserStore, UserStorageUnavailableError
from app.infrastructure.redis_platform_admin import RedisPlatformAdminStore
from app.infrastructure.redis_assistant import RedisAssistantTaskStore, AssistantStorageUnavailableError
from app.service.assistant import AssistantTaskService
from app.service.assistant_file import AssistantFileStore
from app.service.platform_admin import PlatformAdminService
from app.service.user import UserService
from app.service.platform_session import PlatformSessionService
from app.router import router

# 应用入口统一管理业务接口的路径前缀。
API_PREFIX = "/middleware-service/api"


@asynccontextmanager
async def lifespan(application: FastAPI):
    # 在开始接受请求前完成全部校验，避免以不完整身份配置运行。
    application.state.platforms = MappingProxyType(load_platforms_from_env())
    stream_settings = load_stream_settings()
    user_settings = load_user_cache_settings()
    redis_settings = load_redis_settings()
    assistant_settings = load_assistant_task_settings()
    redis_client = create_redis_client(redis_settings)
    application.state.redis = redis_client
    try:
        try:
            await redis_client.ping()
        except RedisError:
            raise RuntimeError("Redis 无法连接或认证失败，应用启动终止。") from None
        await verify_streams(stream_settings)
        application.state.platform_sessions = PlatformSessionService(
            application.state.platforms, load_session_settings(),
            stream_manager=PlatformStreamManager(stream_settings)
        )
        application.state.ingestion = IngestionService(
            application.state.platform_sessions, stream_settings.request_stream,
            deduplicator=IngestionDeduplicator(stream_settings),
        )
        application.state.deletion = DeletionService(
            application.state.platform_sessions, stream_settings.deletion_request_stream,
            stream_settings.registry_platform_code,
            deduplicator=StreamPublishDeduplicator(
                stream_settings, stream_settings.deletion_request_stream,
                key_fields=("platform_code", "source_id"),
                consumer_code=stream_settings.registry_platform_code,
            ),
        )
        application.state.review = ReviewService(
            application.state.platform_sessions, stream_settings.result_stream,
            application.state.platforms, stream_settings.registry_platform_code,
            deduplicator=StreamPublishDeduplicator(
                stream_settings, stream_settings.result_stream,
                key_fields=("target_platform_code", "request_id"),
            ),
        )
        application.state.deletion_review = DeletionReviewService(
            application.state.platform_sessions, stream_settings.deletion_result_stream,
            application.state.platforms, stream_settings.registry_platform_code,
            deduplicator=StreamPublishDeduplicator(
                stream_settings, stream_settings.deletion_result_stream,
                key_fields=("target_platform_code", "request_id"),
            ),
        )
        application.state.file_permission = FilePermissionService(
            application.state.platform_sessions, stream_settings.file_permission_stream,
            application.state.platforms, stream_settings.registry_platform_code,
            deduplicator=StreamPublishDeduplicator(
                stream_settings, stream_settings.file_permission_stream,
                key_fields=("target_platform_code", "source_id"),
            ),
        )
        application.state.file_permission_consumer = ReviewConsumerService(
            application.state.platform_sessions, stream_settings.file_permission_stream,
            stream_settings.registry_platform_code,
            cache_settings=load_consumer_cache_settings("FILE_PERMISSION"),
        )
        application.state.ingestion_consumer = IngestionConsumerService(
            application.state.platform_sessions, stream_settings.request_stream,
            cache_settings=load_consumer_cache_settings(),
        )
        application.state.deletion_consumer = DeletionConsumerService(
            application.state.platform_sessions, stream_settings.deletion_request_stream,
            stream_settings.registry_platform_code,
            cache_settings=load_consumer_cache_settings("DELETION"),
        )
        application.state.review_consumer = ReviewConsumerService(
            application.state.platform_sessions, stream_settings.result_stream,
            stream_settings.registry_platform_code,
            cache_settings=load_consumer_cache_settings("REVIEW"),
        )
        # 两个结果流共用过滤/确认算法，但使用独立缓存与各自的 broker 消费进度。
        application.state.deletion_review_consumer = ReviewConsumerService(
            application.state.platform_sessions, stream_settings.deletion_result_stream,
            stream_settings.registry_platform_code,
            cache_settings=load_consumer_cache_settings("DELETION_REVIEW"),
        )
        application.state.resources = ResourceService(stream_settings.registry_platform_code)
        application.state.users = UserService(
            application.state.platforms, user_settings,
            RedisUserStore(redis_client, redis_settings.key_prefix),
        )
        application.state.assistant_tasks = AssistantTaskService(
            RedisAssistantTaskStore(redis_client, redis_settings.key_prefix,
                                    assistant_settings.max_tasks_per_user,
                                    assistant_settings.lease_seconds,
                                    assistant_settings.receipt_ttl_seconds,
                                    assistant_settings.merge_max_messages,
                                    assistant_settings.merge_max_bytes,
                                    assistant_settings.merge_interval_seconds,
                                    assistant_settings.max_files_per_task),
            AssistantFileStore(max_files=assistant_settings.max_files_per_task,
                               max_file_bytes=assistant_settings.max_file_bytes),
        )
        application.state.registry_platform_code = stream_settings.registry_platform_code
        application.state.platform_admins = PlatformAdminService(
            application.state.platforms,
            RedisPlatformAdminStore(redis_client, redis_settings.key_prefix),
        )
        cleanup_task = asyncio.create_task(application.state.platform_sessions.run_cleanup())
        try:
            yield
        finally:
            # 先停止后台任务，再释放缓存，避免关闭后遗留任务或凭据。
            cleanup_task.cancel()
            with suppress(asyncio.CancelledError):
                await cleanup_task
            await application.state.platform_sessions.clear()
    finally:
        # 只关闭本进程连接池，不清空 Redis，保留资料和仍在有效期内的登录。
        await redis_client.aclose()


def create_app() -> FastAPI:
    application = FastAPI(
        title="中间件接入服务",
        version="0.1.0",
        lifespan=lifespan,
    )
    @application.exception_handler(UserStorageUnavailableError)
    async def user_storage_unavailable(request: Request, error: UserStorageUnavailableError):
        return JSONResponse(status_code=503, headers={"Cache-Control": "no-store"},
                            content={"detail": "用户存储服务暂不可用，操作结果可能不确定，请稍后核对"})

    @application.exception_handler(AssistantStorageUnavailableError)
    async def assistant_storage_unavailable(request: Request, error: AssistantStorageUnavailableError):
        return JSONResponse(status_code=503, headers={"Cache-Control": "no-store"},
                            content={"detail": "助理任务存储暂不可用，操作结果可能不确定；发布请用相同 request_id 重试，领取或确认请核对当前任务"})

    application.include_router(router, prefix=API_PREFIX)
    return application


app = create_app()


if __name__ == "__main__":
    from pathlib import Path

    import uvicorn

    # 热重载需要应用导入字符串；仅监听 app 代码，避免临时业务文件触发重启。
    uvicorn.run(
        "app.main:app", host="127.0.0.1", port=22000,
        reload=True, reload_dirs=[str(Path(__file__).resolve().parent)], workers=1,
    )
