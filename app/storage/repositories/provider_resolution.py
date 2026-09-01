"""Atomic persistence helpers for Stage 25 snapshots and audit attempts."""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from sqlalchemy import func, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.provider_resolution import ProviderCandidate
from app.storage.models.download_lifecycle import DownloadLifecycleJob
from app.storage.models.provider_resolution import (
    DownloadProviderAttemptRecord,
    DownloadProviderCandidateRecord,
)


class ProviderResolutionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_candidates(self, request_id: int) -> list[DownloadProviderCandidateRecord]:
        rows = await self._session.scalars(
            select(DownloadProviderCandidateRecord)
            .where(DownloadProviderCandidateRecord.request_id == request_id)
            .order_by(DownloadProviderCandidateRecord.id)
        )
        return list(rows)

    async def upsert_candidate(
        self, request_id: int, candidate: ProviderCandidate, now: datetime
    ) -> DownloadProviderCandidateRecord:
        capabilities = candidate.media_capabilities
        payload = dict(
            request_id=request_id,
            provider=candidate.provider.value,
            provider_media_id=candidate.provider_media_id,
            match_score=candidate.match.score,
            match_method=candidate.match.method.value,
            match_reasons=list(candidate.match.reasons),
            media_capabilities={
                "known": capabilities.known,
                "supports_lossy": capabilities.supports_lossy,
                "supports_lossless": capabilities.supports_lossless,
                "qualities": [item.value for item in capabilities.qualities],
                "formats": [item.value for item in capabilities.formats],
            },
            source_reference=candidate.source_reference,
            identity_snapshot={
                "title": candidate.media_identity.title,
                "artist": candidate.media_identity.artist,
                "album": candidate.media_identity.album,
                "isrc": candidate.media_identity.isrc,
                "duration_ms": candidate.media_identity.duration_ms,
                "version_markers": sorted(candidate.media_identity.version_markers),
            },
            created_at=now,
            updated_at=now,
        )
        await self._session.execute(
            sqlite_insert(DownloadProviderCandidateRecord)
            .values(**payload)
            .on_conflict_do_update(
                index_elements=[
                    DownloadProviderCandidateRecord.request_id,
                    DownloadProviderCandidateRecord.provider,
                    DownloadProviderCandidateRecord.provider_media_id,
                ],
                set_={
                    "match_score": payload["match_score"],
                    "match_method": payload["match_method"],
                    "match_reasons": payload["match_reasons"],
                    "media_capabilities": payload["media_capabilities"],
                    "source_reference": payload["source_reference"],
                    "identity_snapshot": payload["identity_snapshot"],
                    "updated_at": now,
                },
            )
        )
        row = await self._session.scalar(
            select(DownloadProviderCandidateRecord).where(
                DownloadProviderCandidateRecord.request_id == request_id,
                DownloadProviderCandidateRecord.provider == candidate.provider.value,
                DownloadProviderCandidateRecord.provider_media_id == candidate.provider_media_id,
            )
        )
        if row is None:
            raise RuntimeError("candidate persistence failed")
        return row

    async def start_attempt(
        self,
        *,
        job_id: int,
        candidate_id: int,
        attempt_number: int,
        provider_account_id: str | None,
        now: datetime,
    ) -> DownloadProviderAttemptRecord:
        row = DownloadProviderAttemptRecord(
            job_id=job_id,
            candidate_id=candidate_id,
            provider_account_id=provider_account_id,
            attempt_number=attempt_number,
            started_at=now,
            status="STARTED",
            created_at=now,
            updated_at=now,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def next_attempt_number(self, job_id: int) -> int:
        value = await self._session.scalar(
            select(func.coalesce(func.max(DownloadProviderAttemptRecord.attempt_number), 0)).where(
                DownloadProviderAttemptRecord.job_id == job_id
            )
        )
        return int(value or 0) + 1

    async def list_attempts(self, job_id: int) -> list[DownloadProviderAttemptRecord]:
        rows = await self._session.scalars(
            select(DownloadProviderAttemptRecord)
            .where(DownloadProviderAttemptRecord.job_id == job_id)
            .order_by(DownloadProviderAttemptRecord.attempt_number)
        )
        return list(rows)

    async def successful_provider(self, job_id: int) -> str | None:
        """Return the provider that actually produced the successful artifact."""
        value = await self._session.scalar(
            select(DownloadProviderCandidateRecord.provider)
            .join(
                DownloadProviderAttemptRecord,
                DownloadProviderAttemptRecord.candidate_id == DownloadProviderCandidateRecord.id,
            )
            .where(
                DownloadProviderAttemptRecord.job_id == job_id,
                DownloadProviderAttemptRecord.status == "SUCCEEDED",
            )
            .order_by(DownloadProviderAttemptRecord.attempt_number.desc())
            .limit(1)
        )
        return str(value) if value is not None else None

    async def abandon_unfinished(self, job_id: int, now: datetime) -> int:
        result = await self._session.execute(
            update(DownloadProviderAttemptRecord)
            .where(
                DownloadProviderAttemptRecord.job_id == job_id,
                DownloadProviderAttemptRecord.finished_at.is_(None),
            )
            .values(
                status="ABANDONED",
                finished_at=now,
                failure_code="WORKER_LOST",
                updated_at=now,
            )
        )
        return int(cast(CursorResult[Any], result).rowcount or 0)

    async def abandon_expired_jobs(self, now: datetime) -> int:
        """Reconcile attempts left open by a dead lifecycle worker lease."""
        job_ids = await self._session.scalars(
            select(DownloadLifecycleJob.id).where(
                DownloadLifecycleJob.lease_expires_at.is_not(None),
                DownloadLifecycleJob.lease_expires_at <= now,
            )
        )
        total = 0
        for job_id in job_ids:
            total += await self.abandon_unfinished(int(job_id), now)
        return total

    async def finish_attempt(
        self,
        attempt_id: int,
        *,
        status: str,
        now: datetime,
        failure_code: str | None = None,
        fallback_decision: str | None = None,
    ) -> bool:
        result = await self._session.execute(
            update(DownloadProviderAttemptRecord)
            .where(DownloadProviderAttemptRecord.id == attempt_id)
            .values(
                status=status,
                finished_at=now,
                failure_code=failure_code,
                fallback_decision=fallback_decision,
                updated_at=now,
            )
        )
        return bool(cast(CursorResult[Any], result).rowcount)


__all__ = ["ProviderResolutionRepository"]
