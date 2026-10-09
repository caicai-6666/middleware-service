"""统一注册路由；新增接口按业务模块放入本包。"""

from fastapi import APIRouter

from app.router.health import router as health_router

from app.router.platform import router as platform_router

from app.router.ingestion import router as ingestion_router
from app.router.review import router as review_router

router = APIRouter()
router.include_router(health_router)
router.include_router(platform_router)
router.include_router(ingestion_router)
router.include_router(review_router)
