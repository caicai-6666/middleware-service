"""FastAPI 应用装配与本地启动入口。"""

import asyncio
from contextlib import asynccontextmanager, suppress
from types import MappingProxyType

from fastapi import FastAPI

from app.config.consumer import load_consumer_cache_settings
from app.config.platform import load_platforms_from_env
from app.config.stream import load_stream_settings
from app.service.ingestion_consumer import IngestionConsumerService
from app.service.ingestion import IngestionService
from app.service.review import ReviewService
from app.service.review_consumer import ReviewConsumerService
from app.service.platform_stream import PlatformStreamManager
from app.service.stream import initialize_streams
from app.config.session import load_session_settings
from app.service.platform_session import PlatformSessionService
from app.router import router

# 应用入口统一管理业务接口的路径前缀。
API_PREFIX = "/middleware-service/api"


@asynccontextmanager
async def lifespan(application: FastAPI):
    # 在开始接受请求前完成全部校验，避免以不完整身份配置运行。
    application.state.platforms = MappingProxyType(load_platforms_from_env())
    stream_settings = load_stream_settings()
    await initialize_streams(stream_settings)
    application.state.platform_sessions = PlatformSessionService(
        application.state.platforms, load_session_settings(),
        stream_manager=PlatformStreamManager(stream_settings)
    )
    application.state.ingestion = IngestionService(
        application.state.platform_sessions, stream_settings.request_stream
    )
    application.state.review = ReviewService(
        application.state.platform_sessions, stream_settings.result_stream,
        application.state.platforms, stream_settings.registry_platform_code,
    )
    application.state.ingestion_consumer = IngestionConsumerService(
        application.state.platform_sessions, stream_settings.request_stream,
        cache_settings=load_consumer_cache_settings(),
    )
    application.state.review_consumer = ReviewConsumerService(
        application.state.platform_sessions, stream_settings.result_stream,
        stream_settings.registry_platform_code,
        cache_settings=load_consumer_cache_settings("REVIEW"),
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


def create_app() -> FastAPI:
    application = FastAPI(
        title="中间件接入服务",
        version="0.1.0",
        lifespan=lifespan,
    )
    application.include_router(router, prefix=API_PREFIX)
    return application


app = create_app()


if __name__ == "__main__":
    import uvicorn

    # 本地模块启动仅监听回环地址；容器监听地址由 Dockerfile 指定。
    uvicorn.run(app, host="127.0.0.1", port=22000)
