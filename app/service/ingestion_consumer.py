"""入库申请消费服务，复用申请流的确认与附件清理机制。"""

from pathlib import Path
from app.config.platform import PROJECT_ROOT
from app.config.consumer import ConsumerCacheSettings
from app.service.file_request_consumer import FileRequestConsumerService, AcknowledgedFileCleanupError
from app.service.stream_consumer import (
    Delivery, ConsumptionUnavailableError, InvalidStreamMessageError, AcknowledgementConflictError,
)


class IngestionConsumerService(FileRequestConsumerService):
    def __init__(self, sessions, stream: str, wait_seconds: float = 2,
                 cache_settings: ConsumerCacheSettings | None = None,
                 file_directory: Path | None = None):
        super().__init__(sessions, stream, wait_seconds=wait_seconds, cache_settings=cache_settings,
                         file_directory=file_directory if file_directory is not None
                         else PROJECT_ROOT / "data/ingestion-request/file")
