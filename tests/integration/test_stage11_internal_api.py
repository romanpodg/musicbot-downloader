from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from sqlalchemy import func, select

from app.core.enums import DeepLinkStatus, DeepLinkTargetType, MusicProviderName
from app.core.exceptions import UnsupportedMediaType, UnsupportedProvider
from app.internal_api import create_internal_api_app
from app.providers.base import AlbumReference, TrackReference
from app.services.deep_links import DeepLinkRegistryService
from app.storage import Database
from app.storage.models import DeepLinkRegistryEntry

API_TOKEN = "stage-11-internal-api-token-value-1234567890"
TRACK_URL = "https://open.spotify.com/track/0123456789012345678901"
ALBUM_URL = "https://open.spotify.com/album/0123456789012345678901"


class Stage11Provider:
    async def classify_url(self, url: str) -> TrackReference | AlbumReference:
        if url == TRACK_URL:
            return TrackReference(
                MusicProviderName.SPOTIFY,
                "0123456789012345678901",
                TRACK_URL,
            )
        if url == ALBUM_URL:
            return AlbumReference(
                MusicProviderName.SPOTIFY,
                "0123456789012345678901",
                ALBUM_URL,
            )
        if "/playlist/" in url:
            raise UnsupportedMediaType()
        raise UnsupportedProvider()


@dataclass
class TrackResolver:
    track_id: int
    calls: int = 0
    discover_values: list[bool] | None = None

    async def resolve(self, url: str, *, discover: bool = False) -> SimpleNamespace:
        self.calls += 1
        if self.discover_values is None:
            self.discover_values = []
        self.discover_values.append(discover)
        return SimpleNamespace(track=SimpleNamespace(id=self.track_id))


async def _service(
    database: Database, *, telegram_bot_id: int = 100
) -> tuple[DeepLinkRegistryService, TrackResolver]:
    async with database.transaction() as repositories:
        track = await repositories.tracks.create_track(title="Song", artist="Artist")
    resolver = TrackResolver(track.id)
    return (
        DeepLinkRegistryService(
            database,
            Stage11Provider(),  # type: ignore[arg-type]
            resolver,  # type: ignore[arg-type]
            telegram_bot_id=telegram_bot_id,
        ),
        resolver,
    )


def _client(registry: DeepLinkRegistryService) -> httpx.AsyncClient:
    app = create_internal_api_app(
        api_token=API_TOKEN,
        registry=registry,
        bot_username="stage11_downloader_bot",
    )
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://internal.test",
    )


def _auth(token: str = API_TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_health_is_generic_and_registration_requires_correct_bearer(
    database: Database,
) -> None:
    registry, _ = await _service(database)
    async with _client(registry) as client:
        health = await client.get("/internal/v1/health")
        assert health.status_code == 200
        assert health.json() == {"status": "ok"}
        assert len(health.headers["X-Request-ID"]) == 32
        for headers in ({}, _auth("wrong-token"), {"Authorization": "Basic value"}):
            response = await client.post(
                "/internal/v1/deep-links", json={"url": TRACK_URL}, headers=headers
            )
            assert response.status_code == 401
            assert response.json()["error"]["code"] == "UNAUTHORIZED"


async def test_readiness_is_generic_and_independent_of_provider_health(
    database: Database,
) -> None:
    registry, _ = await _service(database)
    ready = False
    app = create_internal_api_app(
        api_token=API_TOKEN,
        registry=registry,
        bot_username="stage11_downloader_bot",
        readiness=lambda: ready,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://internal.test",
    ) as client:
        starting = await client.get("/internal/v1/ready")
        assert starting.status_code == 503
        assert starting.json() == {"status": "not_ready"}
        ready = True
        available = await client.get("/internal/v1/ready")
        assert available.status_code == 200
        assert available.json() == {"status": "ready"}


async def test_track_and_album_registration_contract_and_safe_rejections(
    database: Database,
) -> None:
    registry, resolver = await _service(database)
    async with _client(registry) as client:
        track = await client.post(
            "/internal/v1/deep-links",
            json={"url": TRACK_URL},
            headers={**_auth(), "Idempotency-Key": "post-track-1"},
        )
        assert track.status_code == 200
        body = track.json()
        assert body["target_type"] == "track"
        assert body["status"] == "active"
        assert body["created"] is True
        assert body["deep_link_url"].endswith(f"?start={body['start_parameter']}")
        assert "0123456789012345678901" not in body["start_parameter"]
        assert resolver.calls == 1
        assert resolver.discover_values == [True]

        album = await client.post(
            "/internal/v1/deep-links", json={"url": ALBUM_URL}, headers=_auth()
        )
        assert album.status_code == 200
        assert album.json()["target_type"] == "album"
        assert resolver.calls == 1

        playlist = await client.post(
            "/internal/v1/deep-links",
            json={"url": "https://open.spotify.com/playlist/0123456789012345678901"},
            headers=_auth(),
        )
        assert playlist.status_code == 422
        assert playlist.json()["error"]["code"] == "UNSUPPORTED_MEDIA_TYPE"

        ssrf = await client.post(
            "/internal/v1/deep-links",
            json={"url": "http://127.0.0.1/private"},
            headers=_auth(),
        )
        assert ssrf.status_code == 422
        assert ssrf.json()["error"]["code"] == "INVALID_MEDIA_URL"
        assert "127.0.0.1" not in ssrf.text


async def test_idempotency_replay_conflict_and_distinct_keys(database: Database) -> None:
    registry, resolver = await _service(database)
    async with _client(registry) as client:
        first = await client.post(
            "/internal/v1/deep-links",
            json={"url": TRACK_URL},
            headers={**_auth(), "Idempotency-Key": "post-1"},
        )
        replay = await client.post(
            "/internal/v1/deep-links",
            json={"url": TRACK_URL},
            headers={**_auth(), "Idempotency-Key": "post-1"},
        )
        conflict = await client.post(
            "/internal/v1/deep-links",
            json={"url": ALBUM_URL},
            headers={**_auth(), "Idempotency-Key": "post-1"},
        )
        second = await client.post(
            "/internal/v1/deep-links",
            json={"url": TRACK_URL},
            headers={**_auth(), "Idempotency-Key": "post-2"},
        )
    assert first.json()["start_parameter"] == replay.json()["start_parameter"]
    assert replay.json()["created"] is False
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_KEY_CONFLICT"
    assert second.json()["start_parameter"] != first.json()["start_parameter"]
    assert resolver.calls == 2


async def test_one_hundred_concurrent_idempotent_registrations_converge(
    database: Database,
) -> None:
    registry, _ = await _service(database)
    results = await asyncio.gather(
        *(
            registry.register_from_url(TRACK_URL, idempotency_key="concurrent-post")
            for _ in range(100)
        )
    )
    assert len({result.entry.token for result in results}) == 1
    assert sum(result.created for result in results) == 1
    async with database.transaction() as repositories:
        count = await repositories.deep_links._session.scalar(  # noqa: SLF001
            select(func.count(DeepLinkRegistryEntry.id))
        )
    assert count == 1


async def test_lookup_revocation_and_bot_identity_scope(database: Database) -> None:
    registry, _ = await _service(database)
    created = await registry.register_from_url(TRACK_URL)
    token = created.entry.token
    assert len(token) == 35
    assert token.startswith("d1_")
    assert token.replace("_", "").replace("-", "").isalnum()

    async with _client(registry) as client:
        lookup = await client.get(f"/internal/v1/deep-links/{token}", headers=_auth())
        assert lookup.status_code == 200
        assert lookup.json()["track_id"] == created.entry.track_id
        revoked = await client.post(f"/internal/v1/deep-links/{token}/revoke", headers=_auth())
        duplicate = await client.post(f"/internal/v1/deep-links/{token}/revoke", headers=_auth())
        assert revoked.json()["status"] == "revoked"
        assert duplicate.json()["revoked_at"] == revoked.json()["revoked_at"]
    assert await registry.resolve_start_payload(token) is None

    other_bot = DeepLinkRegistryService(
        database,
        Stage11Provider(),  # type: ignore[arg-type]
        TrackResolver(created.entry.track_id or 0),  # type: ignore[arg-type]
        telegram_bot_id=200,
    )
    assert await other_bot.resolve_start_payload(token) is None


async def test_concurrent_revocation_is_idempotent(database: Database) -> None:
    registry, _ = await _service(database)
    token = (await registry.register_from_url(TRACK_URL)).entry.token
    revoked = await asyncio.gather(*(registry.revoke(token) for _ in range(20)))
    assert {entry.status for entry in revoked} == {DeepLinkStatus.REVOKED}
    assert len({entry.revoked_at for entry in revoked}) == 1


async def test_token_collision_retries_and_parser_is_strict(database: Database) -> None:
    registry, _ = await _service(database)
    first_random = "A" * 32
    second_random = "B" * 32
    with patch(
        "app.services.deep_links.secrets.token_urlsafe",
        side_effect=[first_random, first_random, second_random],
    ):
        first = await registry.register_from_url(TRACK_URL)
        second = await registry.register_from_url(TRACK_URL)
    assert first.entry.token == f"d1_{first_random}"
    assert second.entry.token == f"d1_{second_random}"
    assert registry.parse_start_parameter(first.entry.token)
    assert not registry.parse_start_parameter("d1_short")
    assert not registry.parse_start_parameter("d1_" + "+" * 32)
    assert not registry.parse_start_parameter("x1_" + "A" * 32)


async def test_bot_token_rotation_semantics_are_bot_id_only(database: Database) -> None:
    registry, _ = await _service(database, telegram_bot_id=777)
    created = await registry.register_from_url(TRACK_URL)
    after_rotation = DeepLinkRegistryService(
        database,
        Stage11Provider(),  # type: ignore[arg-type]
        TrackResolver(created.entry.track_id or 0),  # type: ignore[arg-type]
        telegram_bot_id=777,
    )
    resolved = await after_rotation.resolve_start_payload(created.entry.token)
    assert resolved is not None
    assert resolved.status is DeepLinkStatus.ACTIVE
    assert resolved.target_type is DeepLinkTargetType.TRACK


async def test_authorization_secret_is_not_logged_on_failure(
    database: Database, caplog: pytest.LogCaptureFixture
) -> None:
    registry, _ = await _service(database)
    caplog.set_level(logging.DEBUG)
    secret = "INTERNAL_API_SUPER_SECRET_VALUE_123456789"
    async with _client(registry) as client:
        response = await client.get(
            "/internal/v1/deep-links/d1_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
            headers=_auth(secret),
        )
    assert response.status_code == 401
    assert secret not in caplog.text


async def test_public_deep_link_token_is_not_an_api_credential(database: Database) -> None:
    registry, _ = await _service(database)
    public_token = (await registry.register_from_url(TRACK_URL)).entry.token
    async with _client(registry) as client:
        response = await client.get(
            f"/internal/v1/deep-links/{public_token}", headers=_auth(public_token)
        )
    assert response.status_code == 401
