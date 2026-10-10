"""入库申请的去重配置，算法复用通用流发布去重服务。"""

from app.infrastructure.stream_stats import stream_stats_client
from app.service.stream_deduplication import (
    StreamPublishDeduplicator,
    DuplicateStreamMessageError as DuplicateIngestionError,
    StreamDeduplicationUnavailableError as IngestionDeduplicationUnavailableError,
)


class IngestionDeduplicator(StreamPublishDeduplicator):
    def __init__(self, settings, *, factory=None, stats_client=stream_stats_client):
        super().__init__(settings, settings.request_stream,
                         key_fields=("platform_code", "source_id"),
                         consumer_code=settings.registry_platform_code,
                         factory=factory, stats_client=stats_client)
