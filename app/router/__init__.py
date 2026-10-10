"""统一注册路由；新增接口按业务模块放入本包。"""

from fastapi import APIRouter

from app.router.health import router as health_router

from app.router.platform import router as platform_router

from app.router.ingestion import router as ingestion_router
from app.router.ingestion_review import router as ingestion_review_router

from app.router.deletion import router as deletion_router
from app.router.deletion_review import router as deletion_review_router

from app.router.resource import router as resource_router

router = APIRouter()
router.include_router(health_router)
router.include_router(platform_router)
router.include_router(ingestion_router)
router.include_router(ingestion_review_router)
router.include_router(deletion_router)
router.include_router(deletion_review_router)
router.include_router(resource_router)
