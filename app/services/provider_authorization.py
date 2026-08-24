"""In-process provider-neutral authorization-flow coordination."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Protocol, cast

from app.core.enums import MusicProviderName
from app.core.provider_accounts import (
    ProviderAccountErrorCode,
    ProviderAuthorizationChallenge,
    ProviderAuthorizationMethod,
    ProviderAuthorizationOutcome,
    ProviderAuthorizationOutcomeStatus,
    ProviderAuthorizationRequest,
    ProviderAuthorizationStartOutcome,
    ProviderAuthorizationStartStatus,
    ProviderCompoundCredentialInput,
    ProviderLocalPairingChallenge,
    ProviderSecretInput,
    ProviderSensitiveInputChallenge,
)


class ProviderAuthorizationDriver(Protocol):
    """Compatibility boundary retained for non-device Stage 13.1 drivers/tests."""

    async def authorize(
        self, request: ProviderAuthorizationRequest
    ) -> ProviderAuthorizationOutcome: ...


class BrowserDeviceAuthorizationDriver(Protocol):
    async def start(
        self, request: ProviderAuthorizationRequest
    ) -> ProviderAuthorizationChallenge | ProviderLocalPairingChallenge: ...

    async def wait(
        self, challenge: ProviderAuthorizationChallenge | ProviderLocalPairingChallenge
    ) -> ProviderAuthorizationOutcome: ...

    async def cancel(self, flow_id: str) -> None: ...


class SensitiveSecretAuthorizationDriver(Protocol):
    async def authorize_secret(
        self, credential: ProviderSecretInput
    ) -> ProviderAuthorizationOutcome: ...


class CompoundCredentialAuthorizationDriver(Protocol):
    async def authorize_credentials(
        self, credentials: ProviderCompoundCredentialInput
    ) -> ProviderAuthorizationOutcome: ...


@dataclass(slots=True)
class _ActiveAuthorization:
    flow_id: str
    task: asyncio.Task[ProviderAuthorizationOutcome]
    driver: ProviderAuthorizationDriver | BrowserDeviceAuthorizationDriver
    method: ProviderAuthorizationMethod
    challenge: ProviderAuthorizationChallenge | ProviderLocalPairingChallenge | None = None


@dataclass(slots=True)
class _ActiveSensitiveAuthorization:
    flow_id: str
    completion: asyncio.Future[ProviderAuthorizationOutcome]
    driver: SensitiveSecretAuthorizationDriver | CompoundCredentialAuthorizationDriver
    challenge: ProviderSensitiveInputChallenge
    submission_task: asyncio.Task[ProviderAuthorizationOutcome] | None = None


class ProviderAuthorizationCoordinator:
    """Allow at most one ephemeral authorization flow per provider per process."""

    def __init__(
        self,
        drivers: dict[
            tuple[MusicProviderName, ProviderAuthorizationMethod],
            ProviderAuthorizationDriver
            | BrowserDeviceAuthorizationDriver
            | SensitiveSecretAuthorizationDriver
            | CompoundCredentialAuthorizationDriver,
        ]
        | None = None,
    ) -> None:
        self._drivers = dict(drivers or {})
        self._active: dict[MusicProviderName, _ActiveAuthorization] = {}
        self._sensitive_active: dict[MusicProviderName, _ActiveSensitiveAuthorization] = {}
        self._completed: dict[MusicProviderName, tuple[str, ProviderAuthorizationOutcome]] = {}
        self._cleanup_tasks: set[asyncio.Task[None]] = set()
        self._lock = asyncio.Lock()

    def available_methods(
        self, provider: MusicProviderName
    ) -> tuple[ProviderAuthorizationMethod, ...]:
        return tuple(method for candidate, method in self._drivers if candidate is provider)

    async def is_active(self, provider: MusicProviderName) -> bool:
        async with self._lock:
            active = self._active.get(provider)
            sensitive = self._sensitive_active.get(provider)
            return (active is not None and not active.task.done()) or (
                sensitive is not None and not sensitive.completion.done()
            )

    async def active_method(
        self, provider: MusicProviderName
    ) -> ProviderAuthorizationMethod | None:
        async with self._lock:
            active = self._active.get(provider)
            if active is not None and not active.task.done():
                return active.method
            sensitive = self._sensitive_active.get(provider)
            if sensitive is not None and not sensitive.completion.done():
                return sensitive.challenge.authorization_method
            return None

    async def start(
        self, request: ProviderAuthorizationRequest
    ) -> ProviderAuthorizationStartOutcome:
        driver = self._drivers.get((request.provider, request.method))
        authorize_secret = getattr(driver, "authorize_secret", None)
        if (
            request.method is ProviderAuthorizationMethod.SENSITIVE_SECRET
            and driver is not None
            and callable(authorize_secret)
        ):
            return await self._start_sensitive(
                request, cast(SensitiveSecretAuthorizationDriver, driver)
            )
        authorize_credentials = getattr(driver, "authorize_credentials", None)
        if (
            request.method is ProviderAuthorizationMethod.COMPOUND_CREDENTIALS
            and driver is not None
            and callable(authorize_credentials)
        ):
            return await self._start_sensitive(
                request, cast(CompoundCredentialAuthorizationDriver, driver)
            )
        start_method = getattr(driver, "start", None)
        wait_method = getattr(driver, "wait", None)
        if driver is None or not callable(start_method) or not callable(wait_method):
            return ProviderAuthorizationStartOutcome(
                request.provider,
                ProviderAuthorizationStartStatus.UNSUPPORTED,
                error_code=ProviderAccountErrorCode.AUTHORIZATION_UNSUPPORTED,
            )
        async with self._lock:
            existing = self._active.get(request.provider)
            sensitive = self._sensitive_active.get(request.provider)
            if (existing is not None and not existing.task.done()) or (
                sensitive is not None and not sensitive.completion.done()
            ):
                return ProviderAuthorizationStartOutcome(
                    request.provider, ProviderAuthorizationStartStatus.ALREADY_ACTIVE
                )
            try:
                browser_driver = cast(BrowserDeviceAuthorizationDriver, driver)
                challenge = await browser_driver.start(request)
            except Exception as exc:
                code = getattr(exc, "code", None)
                if not isinstance(code, ProviderAccountErrorCode):
                    code = ProviderAccountErrorCode.AUTHORIZATION_FAILED
                return ProviderAuthorizationStartOutcome(
                    request.provider,
                    ProviderAuthorizationStartStatus.FAILED,
                    error_code=code,
                )
            if challenge.provider is not request.provider:
                return ProviderAuthorizationStartOutcome(
                    request.provider,
                    ProviderAuthorizationStartStatus.FAILED,
                    error_code=ProviderAccountErrorCode.AUTHORIZATION_FAILED,
                )
            task = asyncio.create_task(
                self._run_browser_driver(browser_driver, challenge),
                name=f"provider-authorization-{request.provider.value}-{challenge.flow_id}",
            )
            active = _ActiveAuthorization(
                challenge.flow_id, task, browser_driver, request.method, challenge
            )
            self._active[request.provider] = active
            self._completed.pop(request.provider, None)
            self._schedule_cleanup(request.provider, active)
        return ProviderAuthorizationStartOutcome(
            request.provider, ProviderAuthorizationStartStatus.STARTED, challenge=challenge
        )

    async def wait(self, provider: MusicProviderName, flow_id: str) -> ProviderAuthorizationOutcome:
        task: asyncio.Future[ProviderAuthorizationOutcome] | None = None
        async with self._lock:
            active = self._active.get(provider)
            if active is not None and active.flow_id == flow_id:
                task = active.task
            else:
                sensitive = self._sensitive_active.get(provider)
                if sensitive is not None and sensitive.flow_id == flow_id:
                    task = sensitive.completion
                else:
                    task = None
                completed = self._completed.get(provider)
                if task is None and completed is not None and completed[0] == flow_id:
                    return completed[1]
                if task is None:
                    return ProviderAuthorizationOutcome(
                        provider,
                        ProviderAuthorizationOutcomeStatus.FAILED,
                        ProviderAccountErrorCode.AUTHORIZATION_STALE_FLOW,
                    )
        assert task is not None
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.cancelled():
                return ProviderAuthorizationOutcome(
                    provider, ProviderAuthorizationOutcomeStatus.CANCELLED
                )
            raise

    async def authorize(
        self, request: ProviderAuthorizationRequest
    ) -> ProviderAuthorizationOutcome:
        """Await a complete flow, preserving the accepted Stage 13.1 API."""

        driver = self._drivers.get((request.provider, request.method))
        authorize_method = getattr(driver, "authorize", None)
        if callable(authorize_method):
            return await self._authorize_legacy(request, cast(ProviderAuthorizationDriver, driver))
        started = await self.start(request)
        if started.status is ProviderAuthorizationStartStatus.ALREADY_ACTIVE:
            return ProviderAuthorizationOutcome(
                request.provider, ProviderAuthorizationOutcomeStatus.ALREADY_ACTIVE
            )
        if started.status is not ProviderAuthorizationStartStatus.STARTED:
            return ProviderAuthorizationOutcome(
                request.provider,
                ProviderAuthorizationOutcomeStatus.UNSUPPORTED
                if started.status is ProviderAuthorizationStartStatus.UNSUPPORTED
                else ProviderAuthorizationOutcomeStatus.FAILED,
                started.error_code,
            )
        assert started.challenge is not None
        return await self.wait(request.provider, started.challenge.flow_id)

    async def pending_sensitive_challenge(
        self,
        provider: MusicProviderName,
        method: ProviderAuthorizationMethod | None = None,
    ) -> ProviderSensitiveInputChallenge | None:
        """Return only the current generation while it is awaiting a first submission."""

        async with self._lock:
            active = self._sensitive_active.get(provider)
            if (
                active is None
                or active.completion.done()
                or active.submission_task is not None
                or (method is not None and active.challenge.authorization_method is not method)
            ):
                return None
            return active.challenge

    async def submit_sensitive_secret(
        self,
        provider: MusicProviderName,
        flow_id: str,
        credential: ProviderSecretInput,
    ) -> ProviderAuthorizationOutcome:
        """Generation-bound handoff; the credential is never retained after driver completion."""

        if credential.provider is not provider:
            return self._stale(provider)
        async with self._lock:
            active = self._sensitive_active.get(provider)
            if (
                active is None
                or active.flow_id != flow_id
                or active.completion.done()
                or active.challenge.authorization_method
                is not ProviderAuthorizationMethod.SENSITIVE_SECRET
            ):
                return self._stale(provider)
            if active.submission_task is not None:
                return ProviderAuthorizationOutcome(
                    provider, ProviderAuthorizationOutcomeStatus.ALREADY_ACTIVE
                )
            task = asyncio.create_task(
                self._complete_sensitive_submission(active, credential),
                name=f"provider-sensitive-authorization-{provider.value}-{flow_id}",
            )
            active.submission_task = task
        return await asyncio.shield(task)

    async def submit_compound_credentials(
        self,
        provider: MusicProviderName,
        flow_id: str,
        credentials: ProviderCompoundCredentialInput,
    ) -> ProviderAuthorizationOutcome:
        if credentials.provider is not provider:
            return self._stale(provider)
        async with self._lock:
            active = self._sensitive_active.get(provider)
            if (
                active is None
                or active.flow_id != flow_id
                or active.completion.done()
                or active.challenge.authorization_method
                is not ProviderAuthorizationMethod.COMPOUND_CREDENTIALS
            ):
                return self._stale(provider)
            if active.submission_task is not None:
                return ProviderAuthorizationOutcome(
                    provider, ProviderAuthorizationOutcomeStatus.ALREADY_ACTIVE
                )
            task = asyncio.create_task(
                self._complete_sensitive_submission(active, credentials),
                name=f"provider-compound-authorization-{provider.value}-{flow_id}",
            )
            active.submission_task = task
        return await asyncio.shield(task)

    async def fail_sensitive_input(
        self,
        provider: MusicProviderName,
        flow_id: str,
        code: ProviderAccountErrorCode,
    ) -> ProviderAuthorizationOutcome:
        """Terminate a pending flow without invoking its provider driver."""

        async with self._lock:
            active = self._sensitive_active.get(provider)
            if (
                active is None
                or active.flow_id != flow_id
                or active.completion.done()
                or active.submission_task is not None
            ):
                return self._stale(provider)
            outcome = ProviderAuthorizationOutcome(
                provider, ProviderAuthorizationOutcomeStatus.FAILED, code
            )
            active.completion.set_result(outcome)
            self._completed[provider] = (flow_id, outcome)
            self._sensitive_active.pop(provider, None)
            return outcome

    async def cancel(
        self, provider: MusicProviderName, flow_id: str | None = None
    ) -> ProviderAuthorizationOutcome:
        async with self._lock:
            active = self._active.get(provider)
            sensitive = self._sensitive_active.get(provider)
            if sensitive is not None and not sensitive.completion.done():
                if flow_id is not None and sensitive.flow_id != flow_id:
                    return self._stale(provider)
                if sensitive.submission_task is not None:
                    return ProviderAuthorizationOutcome(
                        provider, ProviderAuthorizationOutcomeStatus.ALREADY_ACTIVE
                    )
                outcome = ProviderAuthorizationOutcome(
                    provider, ProviderAuthorizationOutcomeStatus.CANCELLED
                )
                sensitive.completion.set_result(outcome)
                self._completed[provider] = (sensitive.flow_id, outcome)
                self._sensitive_active.pop(provider, None)
                return outcome
            if active is None or active.task.done():
                completed = self._completed.get(provider)
                if flow_id is not None and completed is not None and completed[0] == flow_id:
                    if completed[1].status is ProviderAuthorizationOutcomeStatus.CANCELLED:
                        return completed[1]
                return ProviderAuthorizationOutcome(
                    provider,
                    ProviderAuthorizationOutcomeStatus.FAILED
                    if flow_id is not None
                    else ProviderAuthorizationOutcomeStatus.UNSUPPORTED,
                    ProviderAccountErrorCode.AUTHORIZATION_STALE_FLOW
                    if flow_id is not None
                    else ProviderAccountErrorCode.AUTHORIZATION_UNSUPPORTED,
                )
            if flow_id is not None and active.flow_id != flow_id:
                return ProviderAuthorizationOutcome(
                    provider,
                    ProviderAuthorizationOutcomeStatus.FAILED,
                    ProviderAccountErrorCode.AUTHORIZATION_STALE_FLOW,
                )
        if active.challenge is not None:
            try:
                await cast(BrowserDeviceAuthorizationDriver, active.driver).cancel(active.flow_id)
            except Exception:
                pass
        active.task.cancel()
        await asyncio.gather(active.task, return_exceptions=True)
        outcome = ProviderAuthorizationOutcome(
            provider, ProviderAuthorizationOutcomeStatus.CANCELLED
        )
        async with self._lock:
            self._completed[provider] = (active.flow_id, outcome)
            if self._active.get(provider) is active:
                self._active.pop(provider, None)
        return outcome

    async def close(self) -> None:
        """Cancel child-side state and every tracked ephemeral task during shutdown."""

        async with self._lock:
            active_flows = tuple(self._active.values())
            sensitive_flows = tuple(self._sensitive_active.values())
        for browser_active in active_flows:
            if browser_active.challenge is not None:
                try:
                    await cast(BrowserDeviceAuthorizationDriver, browser_active.driver).cancel(
                        browser_active.flow_id
                    )
                except Exception:
                    pass
            browser_active.task.cancel()
        if active_flows:
            await asyncio.gather(*(active.task for active in active_flows), return_exceptions=True)
        submitted: list[asyncio.Task[ProviderAuthorizationOutcome]] = []
        for sensitive_active in sensitive_flows:
            if sensitive_active.submission_task is None:
                if not sensitive_active.completion.done():
                    sensitive_active.completion.set_result(
                        ProviderAuthorizationOutcome(
                            sensitive_active.challenge.provider,
                            ProviderAuthorizationOutcomeStatus.CANCELLED,
                        )
                    )
            else:
                submitted.append(sensitive_active.submission_task)
        if submitted:
            await asyncio.gather(*submitted, return_exceptions=True)
        cleanup_tasks = tuple(self._cleanup_tasks)
        if cleanup_tasks:
            await asyncio.gather(*cleanup_tasks, return_exceptions=True)
        async with self._lock:
            self._active.clear()
            self._sensitive_active.clear()
            self._completed.clear()

    async def _authorize_legacy(
        self,
        request: ProviderAuthorizationRequest,
        driver: ProviderAuthorizationDriver,
    ) -> ProviderAuthorizationOutcome:
        async with self._lock:
            existing = self._active.get(request.provider)
            sensitive = self._sensitive_active.get(request.provider)
            if (existing is not None and not existing.task.done()) or (
                sensitive is not None and not sensitive.completion.done()
            ):
                return ProviderAuthorizationOutcome(
                    request.provider, ProviderAuthorizationOutcomeStatus.ALREADY_ACTIVE
                )
            task = asyncio.create_task(
                self._run_legacy_driver(driver, request),
                name=f"provider-authorization-{request.provider.value}",
            )
            active = _ActiveAuthorization("", task, driver, request.method)
            self._active[request.provider] = active
            self._schedule_cleanup(request.provider, active)
        return await asyncio.shield(task)

    async def _run_legacy_driver(
        self,
        driver: ProviderAuthorizationDriver,
        request: ProviderAuthorizationRequest,
    ) -> ProviderAuthorizationOutcome:
        try:
            outcome = await driver.authorize(request)
        except asyncio.CancelledError:
            raise
        except Exception:
            return ProviderAuthorizationOutcome(
                request.provider,
                ProviderAuthorizationOutcomeStatus.FAILED,
                ProviderAccountErrorCode.AUTHORIZATION_FAILED,
            )
        if outcome.provider is not request.provider:
            return ProviderAuthorizationOutcome(
                request.provider,
                ProviderAuthorizationOutcomeStatus.FAILED,
                ProviderAccountErrorCode.AUTHORIZATION_FAILED,
            )
        return outcome

    async def _start_sensitive(
        self,
        request: ProviderAuthorizationRequest,
        driver: SensitiveSecretAuthorizationDriver | CompoundCredentialAuthorizationDriver,
    ) -> ProviderAuthorizationStartOutcome:
        async with self._lock:
            existing = self._active.get(request.provider)
            sensitive = self._sensitive_active.get(request.provider)
            if (existing is not None and not existing.task.done()) or (
                sensitive is not None and not sensitive.completion.done()
            ):
                return ProviderAuthorizationStartOutcome(
                    request.provider, ProviderAuthorizationStartStatus.ALREADY_ACTIVE
                )
            flow_id = uuid.uuid4().hex[:16]
            challenge = ProviderSensitiveInputChallenge(request.provider, flow_id, request.method)
            completion = asyncio.get_running_loop().create_future()
            active = _ActiveSensitiveAuthorization(flow_id, completion, driver, challenge)
            self._sensitive_active[request.provider] = active
            self._completed.pop(request.provider, None)
            cleanup = asyncio.create_task(
                self._release_sensitive_when_done(request.provider, active),
                name=f"provider-sensitive-release-{request.provider.value}-{flow_id}",
            )
            self._cleanup_tasks.add(cleanup)
            cleanup.add_done_callback(self._cleanup_tasks.discard)
        return ProviderAuthorizationStartOutcome(
            request.provider, ProviderAuthorizationStartStatus.STARTED, challenge=challenge
        )

    async def _run_sensitive_driver(
        self,
        driver: SensitiveSecretAuthorizationDriver | CompoundCredentialAuthorizationDriver,
        credential: ProviderSecretInput | ProviderCompoundCredentialInput,
    ) -> ProviderAuthorizationOutcome:
        try:
            if isinstance(credential, ProviderSecretInput):
                outcome = await cast(SensitiveSecretAuthorizationDriver, driver).authorize_secret(
                    credential
                )
            else:
                outcome = await cast(
                    CompoundCredentialAuthorizationDriver, driver
                ).authorize_credentials(credential)
        except asyncio.CancelledError:
            raise
        except Exception:
            return ProviderAuthorizationOutcome(
                credential.provider,
                ProviderAuthorizationOutcomeStatus.FAILED,
                ProviderAccountErrorCode.AUTHORIZATION_FAILED,
            )
        if outcome.provider is not credential.provider:
            return ProviderAuthorizationOutcome(
                credential.provider,
                ProviderAuthorizationOutcomeStatus.FAILED,
                ProviderAccountErrorCode.AUTHORIZATION_FAILED,
            )
        return outcome

    async def _complete_sensitive_submission(
        self,
        active: _ActiveSensitiveAuthorization,
        credential: ProviderSecretInput | ProviderCompoundCredentialInput,
    ) -> ProviderAuthorizationOutcome:
        outcome = await self._run_sensitive_driver(active.driver, credential)
        async with self._lock:
            if not active.completion.done():
                active.completion.set_result(outcome)
        return outcome

    async def _release_sensitive_when_done(
        self, provider: MusicProviderName, active: _ActiveSensitiveAuthorization
    ) -> None:
        try:
            outcome = await asyncio.shield(active.completion)
        except asyncio.CancelledError:
            outcome = ProviderAuthorizationOutcome(
                provider, ProviderAuthorizationOutcomeStatus.CANCELLED
            )
        except BaseException:
            outcome = ProviderAuthorizationOutcome(
                provider,
                ProviderAuthorizationOutcomeStatus.FAILED,
                ProviderAccountErrorCode.AUTHORIZATION_FAILED,
            )
        async with self._lock:
            if self._sensitive_active.get(provider) is active:
                self._sensitive_active.pop(provider, None)
                self._completed[provider] = (active.flow_id, outcome)

    @staticmethod
    def _stale(provider: MusicProviderName) -> ProviderAuthorizationOutcome:
        return ProviderAuthorizationOutcome(
            provider,
            ProviderAuthorizationOutcomeStatus.FAILED,
            ProviderAccountErrorCode.AUTHORIZATION_STALE_FLOW,
        )

    async def _run_browser_driver(
        self,
        driver: BrowserDeviceAuthorizationDriver,
        challenge: ProviderAuthorizationChallenge | ProviderLocalPairingChallenge,
    ) -> ProviderAuthorizationOutcome:
        try:
            outcome = await driver.wait(challenge)
        except asyncio.CancelledError:
            raise
        except Exception:
            return ProviderAuthorizationOutcome(
                challenge.provider,
                ProviderAuthorizationOutcomeStatus.FAILED,
                ProviderAccountErrorCode.AUTHORIZATION_FAILED,
            )
        if outcome.provider is not challenge.provider:
            return ProviderAuthorizationOutcome(
                challenge.provider,
                ProviderAuthorizationOutcomeStatus.FAILED,
                ProviderAccountErrorCode.AUTHORIZATION_FAILED,
            )
        return outcome

    def _schedule_cleanup(self, provider: MusicProviderName, active: _ActiveAuthorization) -> None:
        cleanup = asyncio.create_task(
            self._release_when_done(provider, active),
            name=f"provider-authorization-release-{provider.value}",
        )
        self._cleanup_tasks.add(cleanup)
        cleanup.add_done_callback(self._cleanup_tasks.discard)

    async def _release_when_done(
        self, provider: MusicProviderName, active: _ActiveAuthorization
    ) -> None:
        try:
            outcome = await asyncio.shield(active.task)
        except asyncio.CancelledError:
            outcome = ProviderAuthorizationOutcome(
                provider, ProviderAuthorizationOutcomeStatus.CANCELLED
            )
        except BaseException:
            outcome = ProviderAuthorizationOutcome(
                provider,
                ProviderAuthorizationOutcomeStatus.FAILED,
                ProviderAccountErrorCode.AUTHORIZATION_FAILED,
            )
        async with self._lock:
            if self._active.get(provider) is active:
                self._active.pop(provider, None)
                self._completed[provider] = (active.flow_id, outcome)
