"""Stage 22 durable preference values and deterministic profile resolution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.core.enums import (
    DeliveryMode,
    FormatPreference,
    NativeCodec,
    ProfileFallbackReason,
    QualityPreference,
    QualityProfile,
)
from app.core.models import MediaCapabilities, ProviderCapabilities


class InvalidDownloadPreferences(ValueError):
    """A preference combination that cannot be fulfilled semantically."""


class FormatUnavailable(ValueError):
    """A valid format preference unsupported by the selected source."""


class ProfileUnavailable(ValueError):
    """No mutually supported quality exists for this provider/media item."""


@dataclass(frozen=True, slots=True)
class UserDownloadPreferences:
    user_id: int
    quality: QualityPreference = QualityPreference.BEST_AVAILABLE
    format: FormatPreference = FormatPreference.ORIGINAL
    delivery_mode: DeliveryMode = DeliveryMode.AUDIO
    embed_metadata: bool = True
    embed_cover: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.user_id <= 0:
            raise ValueError("preference user ID must be positive")
        for value, enum_type in (
            (self.quality, QualityPreference),
            (self.format, FormatPreference),
            (self.delivery_mode, DeliveryMode),
        ):
            if not isinstance(value, enum_type):
                raise TypeError(f"invalid preference value: {value!r}")
        if not isinstance(self.embed_metadata, bool) or not isinstance(self.embed_cover, bool):
            raise TypeError("metadata and cover preferences must be boolean")
        if self.quality is QualityPreference.LOSSLESS and self.format in {
            FormatPreference.MP3,
            FormatPreference.M4A,
        }:
            raise InvalidDownloadPreferences("lossless quality cannot be forced to a lossy format")


@dataclass(frozen=True, slots=True)
class EffectiveDownloadProfile:
    requested_quality: QualityPreference
    effective_quality: QualityPreference
    requested_format: FormatPreference
    effective_format: FormatPreference
    delivery_mode: DeliveryMode
    embed_metadata: bool
    embed_cover: bool
    fallback_applied: bool = False
    fallback_reason: ProfileFallbackReason | None = None

    @property
    def quality_profile(self) -> QualityProfile:
        """Map the neutral quality tier to the existing Stage 21 pipeline contract."""

        return {
            QualityPreference.LOSSLESS: QualityProfile.LOSSLESS,
            QualityPreference.HIGH: QualityProfile.MP3_320,
            QualityPreference.STANDARD: QualityProfile.MP3_128,
            QualityPreference.BEST_AVAILABLE: QualityProfile.MP3_320,
        }[self.effective_quality]


class DownloadProfileResolver:
    """Pure deterministic intersection/ranking of preferences and capabilities."""

    def resolve(
        self,
        preferences: UserDownloadPreferences,
        provider_capabilities: ProviderCapabilities,
        media_capabilities: MediaCapabilities | None = None,
    ) -> EffectiveDownloadProfile:
        if media_capabilities is None:
            media_capabilities = provider_capabilities.media
        available = self._qualities(provider_capabilities, media_capabilities)
        if not available:
            raise ProfileUnavailable("no quality is available for the selected media")
        selected, fallback, reason = self._quality(preferences.quality, available)
        effective_format = self._format(
            preferences.format, provider_capabilities, media_capabilities
        )
        return EffectiveDownloadProfile(
            requested_quality=preferences.quality,
            effective_quality=selected,
            requested_format=preferences.format,
            effective_format=effective_format,
            delivery_mode=preferences.delivery_mode,
            embed_metadata=preferences.embed_metadata,
            embed_cover=preferences.embed_cover,
            fallback_applied=fallback,
            fallback_reason=reason,
        )

    @staticmethod
    def _qualities(
        provider: ProviderCapabilities, media: MediaCapabilities
    ) -> frozenset[QualityPreference]:
        if provider.qualities:
            provider_quality = set(provider.qualities)
        elif media.qualities:
            provider_quality = set(media.qualities)
        else:
            provider_quality = set()
            if media.supports_lossy is True or media.native_codecs:
                provider_quality.update((QualityPreference.HIGH, QualityPreference.STANDARD))
            if media.supports_lossless is True or any(
                item.codec is NativeCodec.FLAC for item in media.potential_media
            ):
                provider_quality.add(QualityPreference.LOSSLESS)
        if media.potential_media:
            media_quality: set[QualityPreference] = set()
            if any(item.codec is NativeCodec.FLAC for item in media.potential_media):
                media_quality.add(QualityPreference.LOSSLESS)
            if any(item.codec is not NativeCodec.FLAC for item in media.potential_media):
                media_quality.update((QualityPreference.HIGH, QualityPreference.STANDARD))
            provider_quality &= media_quality
        return frozenset(provider_quality)

    @staticmethod
    def _quality(
        requested: QualityPreference, available: frozenset[QualityPreference]
    ) -> tuple[QualityPreference, bool, ProfileFallbackReason | None]:
        ranking = (
            QualityPreference.LOSSLESS,
            QualityPreference.HIGH,
            QualityPreference.STANDARD,
        )
        if requested is QualityPreference.BEST_AVAILABLE:
            for candidate in ranking:
                if candidate in available:
                    return candidate, False, None
            return QualityPreference.STANDARD, True, ProfileFallbackReason.STANDARD_UNAVAILABLE
        if requested is QualityPreference.LOSSLESS:
            if QualityPreference.LOSSLESS in available:
                return QualityPreference.LOSSLESS, False, None
            for candidate in ranking[1:]:
                if candidate in available:
                    return candidate, True, ProfileFallbackReason.LOSSLESS_UNAVAILABLE
            return QualityPreference.STANDARD, True, ProfileFallbackReason.LOSSLESS_UNAVAILABLE
        if requested is QualityPreference.HIGH:
            if QualityPreference.HIGH in available:
                return QualityPreference.HIGH, False, None
            if QualityPreference.STANDARD in available:
                return QualityPreference.STANDARD, True, ProfileFallbackReason.HIGH_UNAVAILABLE
            return QualityPreference.STANDARD, True, ProfileFallbackReason.HIGH_UNAVAILABLE
        if QualityPreference.STANDARD in available:
            return QualityPreference.STANDARD, False, None
        if QualityPreference.HIGH in available:
            return QualityPreference.HIGH, True, ProfileFallbackReason.STANDARD_UNAVAILABLE
        if QualityPreference.LOSSLESS in available:
            return QualityPreference.LOSSLESS, True, ProfileFallbackReason.STANDARD_UNAVAILABLE
        return QualityPreference.STANDARD, True, ProfileFallbackReason.STANDARD_UNAVAILABLE

    @staticmethod
    def _format(
        requested: FormatPreference,
        provider: ProviderCapabilities,
        media: MediaCapabilities,
    ) -> FormatPreference:
        if requested is FormatPreference.ORIGINAL:
            return requested
        supported = set(provider.formats or media.formats)
        if not supported:
            containers = media.native_containers
            supported = {FormatPreference.FLAC for item in containers if item.value == "flac"}
            if any(item.value == "mp3" for item in containers):
                supported.add(FormatPreference.MP3)
            if any(item.value == "m4a" for item in containers):
                supported.add(FormatPreference.M4A)
        if requested not in supported:
            raise FormatUnavailable(f"format {requested.value} is unavailable")
        return requested


def default_preferences(user_id: int) -> UserDownloadPreferences:
    return UserDownloadPreferences(user_id=user_id)


def profile_to_dict(profile: EffectiveDownloadProfile) -> dict[str, object]:
    """Stable serialization used by persistence adapters and diagnostics."""

    return {
        "requested_quality": profile.requested_quality.value,
        "effective_quality": profile.effective_quality.value,
        "requested_format": profile.requested_format.value,
        "effective_format": profile.effective_format.value,
        "delivery_mode": profile.delivery_mode.value,
        "embed_metadata": profile.embed_metadata,
        "embed_cover": profile.embed_cover,
        "fallback_applied": profile.fallback_applied,
        "fallback_reason": profile.fallback_reason.value if profile.fallback_reason else None,
    }
