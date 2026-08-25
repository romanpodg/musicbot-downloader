"""Stage 18 metadata-processing extension point; no processor is composed yet."""

from __future__ import annotations

from typing import Protocol

from app.core.models import UploadRequest


class MetadataProcessor(Protocol):
    """Future processors may transform an already validated artifact before delivery."""

    async def process(self, artifact: UploadRequest) -> UploadRequest:
        """Return the artifact to deliver without changing queue or provider ownership."""
