"""Authoritative Stage 25 candidate/account execution for download workers."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from app.core.download_preferences import EffectiveDownloadProfile
from app.core.enums import (
    DownloadFailureCode,
    MusicProviderName,
    ProviderAttemptStatus,
    QualityProfile,
)
from app.core.exceptions import DownloadPipelineError
from app.core.models import DownloadResult
from app.core.provider_resolution import (
    CanonicalMediaIdentity,
    ProviderCandidate,
    ProviderCandidateRanker,
)
from app.services.provider_account_selection import ProviderAccountHealth, ProviderAccountSelector
from app.services.provider_candidates import ProviderCandidateResolver
from app.services.provider_fallback import FallbackDecision, fallback_decision
from app.storage import Database
from app.storage.models import DownloadJob
from app.storage.models.download_lifecycle import DownloadRequestRecord


class _Pipeline(Protocol):
    async def download_selected(
        self,
        track_id: int,
        quality_profile: QualityProfile,
        *,
        provider: MusicProviderName,
        provider_media_id: str,
        account_id: str | None = None,
        profile: EffectiveDownloadProfile | None = None,
    ) -> DownloadResult: ...


class _AccountProvider(Protocol):
    async def list_provider_accounts(self, provider: MusicProviderName) -> tuple[str, ...]: ...


class Stage25DownloadExecutor:
    """Concrete implementation of the existing Stage25ExecutionBoundary."""

    def __init__(
        self,
        database: Database,
        pipeline: _Pipeline,
        provider: _AccountProvider,
        resolver: ProviderCandidateResolver,
        ranker: ProviderCandidateRanker,
        *,
        selector: ProviderAccountSelector | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._database = database
        self._pipeline = pipeline
        self._provider = provider
        self._resolver = resolver
        self._ranker = ranker
        self._selector = selector or ProviderAccountSelector()
        self._clock = clock

    async def download(self, job: DownloadJob) -> DownloadResult:
        request, identity = await self._request_identity(job)
        candidates = await self._resolver.resolve(
            identity,
            source_provider=request.provider if request else None,
            source_media_id=request.provider_media_id if request else None,
            request_id=request.id if request else None,
        )
        ranked = self._ranker.rank(
            candidates,
            source_provider=request.provider if request else None,
            profile=request.effective_profile if request else None,
            exact_replay=bool(request and request.replay_of_request_id),
        )
        if not ranked:
            raise DownloadPipelineError(DownloadFailureCode.NO_AVAILABLE_PROVIDER)

        lifecycle_id = await self._lifecycle_id(request.id if request else None)
        failures: list[DownloadFailureCode] = []
        for candidate in ranked:
            accounts = await self._accounts(candidate.provider)
            accountless = not accounts
            if not accounts:
                accounts = (None,)
            attempted: set[str] = set()
            while True:
                account = (
                    None
                    if accountless and not attempted
                    else await self._next_account(candidate, accounts, attempted)
                )
                if account is None and (not accountless or attempted):
                    break
                if account is not None:
                    attempted.add(account)
                else:
                    attempted.add("")
                attempt_id = await self._start_attempt(
                    lifecycle_id, request.id if request else None, candidate, account
                )
                try:
                    try:
                        result = await self._pipeline.download_selected(
                            job.track_id,
                            job.quality_profile,
                            profile=request.effective_profile if request else None,
                            provider=candidate.provider,
                            provider_media_id=candidate.provider_media_id,
                            account_id=account,
                        )
                    except TypeError as exc:
                        if "profile" not in str(exc) and "argument" not in str(exc):
                            raise
                        result = await self._pipeline.download_selected(
                            job.track_id,
                            job.quality_profile,
                            provider=candidate.provider,
                            provider_media_id=candidate.provider_media_id,
                            account_id=account,
                        )
                except DownloadPipelineError as exc:
                    code = exc.code
                    failures.append(code)
                    another_account = any(
                        item not in attempted for item in accounts if item is not None
                    )
                    another_provider = any(
                        item.provider is not candidate.provider for item in ranked
                    )
                    decision = fallback_decision(
                        code,
                        another_account=another_account,
                        another_provider=another_provider,
                    )
                    await self._finish_attempt(attempt_id, code, decision)
                    await self._update_health(candidate, account, code)
                    if decision is FallbackDecision.SAME_PROVIDER_NEXT_ACCOUNT:
                        continue
                    if decision is FallbackDecision.NEXT_PROVIDER:
                        break
                    raise DownloadPipelineError(code) from exc
                except Exception:
                    code = DownloadFailureCode.PROVIDER_ERROR
                    failures.append(code)
                    decision = fallback_decision(
                        code,
                        another_account=any(
                            item not in attempted for item in accounts if item is not None
                        ),
                        another_provider=any(
                            item.provider is not candidate.provider for item in ranked
                        ),
                    )
                    await self._finish_attempt(attempt_id, code, decision)
                    await self._update_health(candidate, account, code)
                    if decision is FallbackDecision.SAME_PROVIDER_NEXT_ACCOUNT:
                        continue
                    if decision is FallbackDecision.NEXT_PROVIDER:
                        break
                    raise DownloadPipelineError(code) from None
                else:
                    await self._finish_attempt(attempt_id, None, None, success=True)
                    await self._update_health(candidate, account, None)
                    return result

        if failures and any(
            code
            in {
                DownloadFailureCode.PROVIDER_TEMPORARY,
                DownloadFailureCode.NETWORK,
                DownloadFailureCode.PROVIDER_RATE_LIMITED,
            }
            for code in failures
        ):
            raise DownloadPipelineError(DownloadFailureCode.PROVIDER_TEMPORARY)
        raise DownloadPipelineError(
            failures[-1] if failures else DownloadFailureCode.NO_AVAILABLE_PROVIDER
        )

    async def _request_identity(
        self, job: DownloadJob
    ) -> tuple[DownloadRequestRecord | None, CanonicalMediaIdentity]:
        async with self._database.transaction() as repositories:
            request = await repositories.download_lifecycle.latest_request_for_track(job.track_id)
            track = await repositories.tracks.get_track_by_id(job.track_id)
        if track is None:
            raise DownloadPipelineError(DownloadFailureCode.NO_AVAILABLE_PROVIDER)
        identity = CanonicalMediaIdentity.from_values(
            title=track.title or "Unknown",
            artist=track.artist or "Unknown",
            album=track.album,
            isrc=track.isrc,
            duration_ms=track.duration_ms,
        )
        return request, identity

    async def _lifecycle_id(self, request_id: int | None) -> int:
        if request_id is None:
            raise DownloadPipelineError(DownloadFailureCode.NO_AVAILABLE_PROVIDER)
        async with self._database.transaction() as repositories:
            lifecycle = await repositories.download_lifecycle.get_job_for_request(request_id)
        if lifecycle is None:
            raise DownloadPipelineError(DownloadFailureCode.NO_AVAILABLE_PROVIDER)
        return lifecycle.id

    async def _accounts(self, provider: MusicProviderName) -> tuple[str | None, ...]:
        method = getattr(self._provider, "list_provider_accounts", None)
        if not callable(method):
            return ()
        values = await method(provider)
        return tuple(str(value) for value in values if str(value))

    async def _next_account(
        self,
        candidate: ProviderCandidate,
        accounts: tuple[str | None, ...],
        attempted: set[str],
    ) -> str | None:
        remaining = tuple(item for item in accounts if item not in attempted)
        if not remaining:
            return None
        if remaining[0] is None:
            return None
        async with self._database.transaction() as repositories:
            eligible = await self._selector.eligible_durable(
                repositories.provider_account_health,
                candidate.provider,
                [item for item in remaining if item is not None],
            )
        return next(
            (item.account_id for item in eligible if item.account_id not in attempted), None
        )

    async def _start_attempt(
        self,
        lifecycle_id: int,
        request_id: int | None,
        candidate: ProviderCandidate,
        account: str | None,
    ) -> int:
        if request_id is None:
            raise DownloadPipelineError(DownloadFailureCode.NO_AVAILABLE_PROVIDER)
        async with self._database.transaction() as repositories:
            rows = await repositories.provider_resolution.list_candidates(request_id)
            row = next(
                (
                    item
                    for item in rows
                    if item.provider == candidate.provider.value
                    and item.provider_media_id == candidate.provider_media_id
                ),
                None,
            )
            if row is None:
                row = await repositories.provider_resolution.upsert_candidate(
                    request_id, candidate, self._now()
                )
            number = await repositories.provider_resolution.next_attempt_number(lifecycle_id)
            attempt = await repositories.provider_resolution.start_attempt(
                job_id=lifecycle_id,
                candidate_id=row.id,
                attempt_number=number,
                provider_account_id=account,
                now=self._now(),
            )
            return attempt.id

    async def _finish_attempt(
        self,
        attempt_id: int,
        code: DownloadFailureCode | None,
        decision: FallbackDecision | None,
        *,
        success: bool = False,
    ) -> None:
        async with self._database.transaction() as repositories:
            await repositories.provider_resolution.finish_attempt(
                attempt_id,
                status=(
                    ProviderAttemptStatus.SUCCEEDED.value
                    if success
                    else ProviderAttemptStatus.FAILED.value
                ),
                now=self._now(),
                failure_code=code.value if code else None,
                fallback_decision=decision.value if decision else None,
            )

    async def _update_health(
        self,
        candidate: ProviderCandidate,
        account: str | None,
        code: DownloadFailureCode | None,
    ) -> None:
        if account is None:
            return
        if code in {
            DownloadFailureCode.MEDIA_NOT_FOUND,
            DownloadFailureCode.MEDIA_UNAVAILABLE,
            DownloadFailureCode.SOURCE_UNAVAILABLE,
            DownloadFailureCode.SOURCE_REQUIREMENT_MISMATCH,
            DownloadFailureCode.PROCESSING,
            DownloadFailureCode.TRANSCODE_FAILED,
            DownloadFailureCode.OUTPUT_VALIDATION_FAILED,
            DownloadFailureCode.TEMP_STORAGE_UNAVAILABLE,
        }:
            return
        async with self._database.transaction() as repositories:
            current = await repositories.provider_account_health.get(candidate.provider, account)
            health = current or ProviderAccountHealth(candidate.provider, account)
            if code is None:
                await self._selector.record_success_durable(
                    repositories.provider_account_health, health
                )
            elif code in {DownloadFailureCode.PROVIDER_AUTH, DownloadFailureCode.AUTH_REQUIRED}:
                await self._selector.record_failure_durable(
                    repositories.provider_account_health, health, auth=True
                )
            elif code is DownloadFailureCode.PROVIDER_RATE_LIMITED:
                await self._selector.record_failure_durable(
                    repositories.provider_account_health, health, rate_limited=True
                )
            else:
                await self._selector.record_failure_durable(
                    repositories.provider_account_health, health
                )

    def _now(self) -> datetime:
        return self._clock() if self._clock is not None else datetime.now(UTC)


__all__ = ["Stage25DownloadExecutor"]
