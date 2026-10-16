"""注册中心消费无附件删除申请；兼容历史带附件消息的确认和清理。"""

from pathlib import Path
from app.config.platform import PROJECT_ROOT
from app.config.consumer import ConsumerCacheSettings
from app.service.file_request_consumer import FileRequestConsumerService


class DeletionConsumerService(FileRequestConsumerService):
    def __init__(self, sessions, stream: str, registry_code: str, wait_seconds: float = 2,
                 cache_settings: ConsumerCacheSettings | None = None,
                 file_directory: Path | None = None):
        # 新消息无 file 字段，ack 只确认进度；保留历史目录以完成升级前申请的清理。
        super().__init__(sessions, stream, registry_code=registry_code,
                         wait_seconds=wait_seconds, cache_settings=cache_settings,
                         file_directory=file_directory if file_directory is not None
                         else PROJECT_ROOT / "data/deletion-request/file")
