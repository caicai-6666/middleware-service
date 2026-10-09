"""应用存活检查，不探测 RabbitMQ 等外部依赖。"""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter(tags=["健康检查"])


class HealthResponse(BaseModel):
    status: Literal["ok"] = Field(description="应用能够响应请求时固定返回 ok。")


@router.get("/health", response_model=HealthResponse, summary="应用存活检查")
async def health() -> HealthResponse:
    return HealthResponse(status="ok")
