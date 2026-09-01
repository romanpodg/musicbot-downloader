"""Localized Track Cards, keyboards, and compact callback codecs."""

from __future__ import annotations

from dataclasses import dataclass

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.core.enums import QualityProfile
from app.i18n import LocalizationService
from app.services.batch_download import BatchProgress
from app.services.telegram_albums import AlbumCard, AlbumSelectionPage
from app.services.telegram_requests import TrackCard

QUALITY_CODES = {
    QualityProfile.MP3_128: "1",
    QualityProfile.MP3_320: "2",
    QualityProfile.AAC_256: "3",
    QualityProfile.LOSSLESS: "4",
}
CODE_QUALITIES = {value: key for key, value in QUALITY_CODES.items()}


@dataclass(frozen=True, slots=True)
class FirstQualityCallback:
    request_id: int
    quality_profile: QualityProfile


@dataclass(frozen=True, slots=True)
class TrackQualityCallback:
    request_id: int
    quality_profile: QualityProfile


@dataclass(frozen=True, slots=True)
class AlbumQualityCallback:
    request_id: int
    quality_profile: QualityProfile


@dataclass(frozen=True, slots=True)
class AlbumToggleCallback:
    request_id: int
    item_id: int
    page: int


@dataclass(frozen=True, slots=True)
class AlbumPageCallback:
    request_id: int
    page: int


@dataclass(frozen=True, slots=True)
class HistoryCallback:
    action: str
    identifier: int | None = None
    cursor: str | None = None


def encode_first_quality(request_id: int, quality: QualityProfile) -> str:
    if request_id <= 0:
        raise ValueError("invalid request ID")
    return f"q1:{request_id}:{QUALITY_CODES[quality]}"


def parse_first_quality(value: str | None) -> FirstQualityCallback | None:
    if not value:
        return None
    parts = value.split(":")
    if len(parts) != 3 or parts[0] != "q1" or parts[2] not in CODE_QUALITIES:
        return None
    try:
        request_id = int(parts[1])
    except ValueError:
        return None
    if request_id <= 0:
        return None
    return FirstQualityCallback(request_id, CODE_QUALITIES[parts[2]])


def encode_track_download(request_id: int) -> str:
    return _encode_request("td1", request_id)


def parse_track_download(value: str | None) -> int | None:
    return _parse_request(value, "td1")


def encode_other_quality(request_id: int) -> str:
    return _encode_request("to1", request_id)


def parse_other_quality(value: str | None) -> int | None:
    return _parse_request(value, "to1")


def encode_track_quality(request_id: int, quality: QualityProfile) -> str:
    if request_id <= 0:
        raise ValueError("invalid request ID")
    return f"tq1:{request_id}:{QUALITY_CODES[quality]}"


def parse_track_quality(value: str | None) -> TrackQualityCallback | None:
    if not value:
        return None
    parts = value.split(":")
    if len(parts) != 3 or parts[0] != "tq1" or parts[2] not in CODE_QUALITIES:
        return None
    request_id = _positive_int(parts[1])
    if request_id is None:
        return None
    return TrackQualityCallback(request_id, CODE_QUALITIES[parts[2]])


def encode_track_back(request_id: int) -> str:
    return _encode_request("tb1", request_id)


def parse_track_back(value: str | None) -> int | None:
    return _parse_request(value, "tb1")


def encode_album_first_quality(request_id: int, quality: QualityProfile) -> str:
    return _encode_quality("af1", request_id, quality)


def parse_album_first_quality(value: str | None) -> AlbumQualityCallback | None:
    return _parse_album_quality(value, "af1")


def encode_album_download_all(request_id: int) -> str:
    return _encode_request("ad1", request_id)


def parse_album_download_all(value: str | None) -> int | None:
    return _parse_request(value, "ad1")


def encode_album_select_tracks(request_id: int) -> str:
    return _encode_request("as1", request_id)


def parse_album_select_tracks(value: str | None) -> int | None:
    return _parse_request(value, "as1")


def encode_album_other_quality(request_id: int) -> str:
    return _encode_request("ao1", request_id)


def parse_album_other_quality(value: str | None) -> int | None:
    return _parse_request(value, "ao1")


def encode_album_quality(request_id: int, quality: QualityProfile) -> str:
    return _encode_quality("aq1", request_id, quality)


def parse_album_quality(value: str | None) -> AlbumQualityCallback | None:
    return _parse_album_quality(value, "aq1")


def encode_album_toggle(request_id: int, item_id: int, page: int) -> str:
    if min(request_id, item_id) <= 0 or page < 0:
        raise ValueError("invalid album callback")
    return f"at1:{request_id}:{item_id}:{page}"


def parse_album_toggle(value: str | None) -> AlbumToggleCallback | None:
    if not value:
        return None
    parts = value.split(":")
    if len(parts) != 4 or parts[0] != "at1":
        return None
    request_id = _positive_int(parts[1])
    item_id = _positive_int(parts[2])
    page = _nonnegative_int(parts[3])
    if request_id is None or item_id is None or page is None:
        return None
    return AlbumToggleCallback(request_id, item_id, page)


def encode_album_page(request_id: int, page: int) -> str:
    if request_id <= 0 or page < 0:
        raise ValueError("invalid album page")
    return f"ap1:{request_id}:{page}"


def parse_album_page(value: str | None) -> AlbumPageCallback | None:
    if not value:
        return None
    parts = value.split(":")
    if len(parts) != 3 or parts[0] != "ap1":
        return None
    request_id = _positive_int(parts[1])
    page = _nonnegative_int(parts[2])
    if request_id is None or page is None:
        return None
    return AlbumPageCallback(request_id, page)


def encode_history_list(cursor: str | None = None) -> str:
    value = "h24:list" if not cursor else f"h24:list:{cursor}"
    if len(value.encode()) > 64:
        raise ValueError("history callback exceeds Telegram limit")
    return value


def encode_history_track(request_id: int) -> str:
    return f"h24:track:{request_id}"


def encode_history_batch(batch_id: int) -> str:
    return f"h24:batch:{batch_id}"


def encode_history_repeat(request_id: int) -> str:
    return f"h24:repeat:{request_id}"


def encode_history_batch_repeat(batch_id: int) -> str:
    return f"h24:brepeat:{batch_id}"


def parse_history_callback(value: str | None) -> HistoryCallback | None:
    if not value or len(value.encode()) > 64 or not value.startswith("h24:"):
        return None
    parts = value.split(":", 2)
    if parts[1] == "list":
        return HistoryCallback("list", cursor=parts[2] if len(parts) == 3 else None)
    if len(parts) != 3:
        return None
    try:
        identifier = int(parts[2])
    except ValueError:
        return None
    if identifier <= 0 or parts[1] not in {"track", "batch", "repeat", "brepeat"}:
        return None
    return HistoryCallback(parts[1], identifier)


def encode_album_select_all(request_id: int) -> str:
    return _encode_request("ax1", request_id)


def parse_album_select_all(value: str | None) -> int | None:
    return _parse_request(value, "ax1")


def encode_album_clear_all(request_id: int) -> str:
    return _encode_request("ac1", request_id)


def parse_album_clear_all(value: str | None) -> int | None:
    return _parse_request(value, "ac1")


def encode_album_download_selected(request_id: int) -> str:
    return _encode_request("aa1", request_id)


def parse_album_download_selected(value: str | None) -> int | None:
    return _parse_request(value, "aa1")


def encode_album_selection_back(request_id: int) -> str:
    return _encode_request("ab1", request_id)


def parse_album_selection_back(value: str | None) -> int | None:
    return _parse_request(value, "ab1")


def encode_album_quality_back(request_id: int) -> str:
    return _encode_request("ak1", request_id)


def parse_album_quality_back(value: str | None) -> int | None:
    return _parse_request(value, "ak1")


def encode_batch_download(batch_id: int) -> str:
    return _encode_request("bd1", batch_id)


def parse_batch_download(value: str | None) -> int | None:
    return _parse_request(value, "bd1")


def encode_batch_cancel(batch_id: int) -> str:
    return _encode_request("bc1", batch_id)


def parse_batch_cancel(value: str | None) -> int | None:
    return _parse_request(value, "bc1")


def encode_batch_retry(batch_id: int) -> str:
    return _encode_request("br1", batch_id)


def parse_batch_retry(value: str | None) -> int | None:
    return _parse_request(value, "br1")


def encode_setting_quality(quality: QualityProfile) -> str:
    return f"sq1:{QUALITY_CODES[quality]}"


def parse_setting_quality(value: str | None) -> QualityProfile | None:
    if not value:
        return None
    parts = value.split(":")
    return CODE_QUALITIES.get(parts[1]) if len(parts) == 2 and parts[0] == "sq1" else None


def encode_locale(locale: str) -> str:
    return f"l1:{locale}"


def parse_locale(value: str | None) -> str | None:
    if not value:
        return None
    parts = value.split(":")
    return parts[1] if len(parts) == 2 and parts[0] == "l1" and parts[1] in {"en", "ru"} else None


class TelegramPresentation:
    def __init__(self, i18n: LocalizationService) -> None:
        self.i18n = i18n

    def text(self, key: str, locale: str, **values: object) -> str:
        return self.i18n.translate(key, locale, **values)

    def history_text(self, page: object, locale: str) -> str:
        from app.services.download_history import BatchHistoryEntry, TrackHistoryEntry

        entries = getattr(page, "entries", ())
        lines = ["🕘 Download history", ""]
        for number, entry in enumerate(entries, 1):
            if isinstance(entry, TrackHistoryEntry):
                label = f"{entry.artist} — {entry.title}"
                state = "✓ Delivered" if entry.delivered else ("✗ " + entry.status.title())
            elif isinstance(entry, BatchHistoryEntry):
                label = entry.title
                state = f"✓ {entry.succeeded_items} / {entry.total_items}"
            else:
                continue
            lines.extend((f"{number}. {label}", f"   {state}", ""))
        return "\n".join(lines).rstrip() or "🕘 Download history\n\nNo downloads yet."

    def history_keyboard(self, page: object, locale: str) -> InlineKeyboardMarkup | None:
        from app.services.download_history import BatchHistoryEntry, TrackHistoryEntry

        rows = []
        for entry in getattr(page, "entries", ()):
            if isinstance(entry, TrackHistoryEntry):
                rows.append(
                    [
                        InlineKeyboardButton(
                            text=_bounded(f"{entry.artist} — {entry.title}", 48),
                            callback_data=encode_history_track(entry.request_id),
                        )
                    ]
                )
            elif isinstance(entry, BatchHistoryEntry):
                rows.append(
                    [
                        InlineKeyboardButton(
                            text=_bounded(entry.title, 48),
                            callback_data=encode_history_batch(entry.batch_id),
                        )
                    ]
                )
        cursor = getattr(page, "next_cursor", None)
        if cursor:
            rows.append([InlineKeyboardButton(text="→", callback_data=encode_history_list(cursor))])
        return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None

    def history_track_text(self, entry: object, locale: str) -> str:
        from app.services.download_history import TrackHistoryEntry

        if not isinstance(entry, TrackHistoryEntry):
            return "Download history entry unavailable"
        lines = [f"{entry.artist} — {entry.title}"]
        if entry.album:
            lines.append(entry.album)
        if entry.delivered_at is not None:
            lines.append(f"Delivered: {entry.delivered_at.isoformat()}")
        if entry.provider:
            lines.append(f"Provider: {entry.provider}")
        if entry.profile:
            lines.extend(
                (
                    "",
                    f"Quality: {entry.profile.effective_quality.value}",
                    f"Format: {entry.profile.effective_format.value}",
                )
            )
        lines.extend(("", "✓ Delivered" if entry.delivered else f"✗ {entry.status.title()}"))
        return "\n".join(lines)

    def history_track_keyboard(self, entry: object, locale: str) -> InlineKeyboardMarkup:
        from app.services.download_history import TrackHistoryEntry

        rows = []
        if isinstance(entry, TrackHistoryEntry) and entry.repeat_available:
            rows.append(
                [
                    InlineKeyboardButton(
                        text="Send again", callback_data=encode_history_repeat(entry.request_id)
                    )
                ]
            )
        rows.append([InlineKeyboardButton(text="Back", callback_data=encode_history_list())])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    def history_batch_text(self, entry: object, locale: str) -> str:
        from app.services.download_history import BatchHistoryEntry

        if not isinstance(entry, BatchHistoryEntry):
            return "Download history entry unavailable"
        return (
            f"{entry.title}\n{entry.creator or ''}\n\n"
            f"✓ {entry.succeeded_items} delivered\n✗ {entry.failed_items} failed\n"
            f"Status: {entry.status.title()}"
        )

    def history_batch_keyboard(self, entry: object, locale: str) -> InlineKeyboardMarkup:
        from app.services.download_history import BatchHistoryEntry

        rows = []
        if isinstance(entry, BatchHistoryEntry) and entry.repeat_available:
            rows.append(
                [
                    InlineKeyboardButton(
                        text="Download again",
                        callback_data=encode_history_batch_repeat(entry.batch_id),
                    )
                ]
            )
        rows.append([InlineKeyboardButton(text="Back", callback_data=encode_history_list())])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    def quality_keyboard(
        self,
        locale: str,
        *,
        request_id: int | None = None,
        album_request_id: int | None = None,
    ) -> InlineKeyboardMarkup:
        if request_id is not None and album_request_id is not None:
            raise ValueError("quality picker can target only one request")
        rows = []
        for quality in QualityProfile:
            if request_id is not None:
                callback = encode_first_quality(request_id, quality)
            elif album_request_id is not None:
                callback = encode_album_first_quality(album_request_id, quality)
            else:
                callback = encode_setting_quality(quality)
            rows.append(
                [
                    InlineKeyboardButton(
                        text=self.text(f"quality.{quality.value}.label", locale),
                        callback_data=callback,
                    )
                ]
            )
        return InlineKeyboardMarkup(inline_keyboard=rows)

    def track_card_keyboard(
        self, locale: str, *, request_id: int, quality: QualityProfile
    ) -> InlineKeyboardMarkup:
        quality_name = self.quality_name(quality, locale)
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=self.text("bot.download_quality_button", locale, quality=quality_name),
                        callback_data=encode_track_download(request_id),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=self.text("bot.other_quality_button", locale),
                        callback_data=encode_other_quality(request_id),
                    )
                ],
            ]
        )

    def track_quality_keyboard(self, locale: str, *, request_id: int) -> InlineKeyboardMarkup:
        choices = [
            InlineKeyboardButton(
                text=self.quality_name(quality, locale),
                callback_data=encode_track_quality(request_id, quality),
            )
            for quality in QualityProfile
        ]
        return InlineKeyboardMarkup(
            inline_keyboard=[
                choices[:2],
                choices[2:],
                [
                    InlineKeyboardButton(
                        text=self.text("bot.back_button", locale),
                        callback_data=encode_track_back(request_id),
                    )
                ],
            ]
        )

    def album_card_keyboard(
        self, locale: str, *, request_id: int, quality: QualityProfile
    ) -> InlineKeyboardMarkup:
        quality_name = self.quality_name(quality, locale)
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=self.text("bot.album_download_all", locale, quality=quality_name),
                        callback_data=encode_album_download_all(request_id),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=self.text("bot.album_select_tracks", locale),
                        callback_data=encode_album_select_tracks(request_id),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=self.text("bot.album_other_quality", locale),
                        callback_data=encode_album_other_quality(request_id),
                    )
                ],
            ]
        )

    def album_quality_keyboard(self, locale: str, *, request_id: int) -> InlineKeyboardMarkup:
        choices = [
            InlineKeyboardButton(
                text=self.quality_name(quality, locale),
                callback_data=encode_album_quality(request_id, quality),
            )
            for quality in QualityProfile
        ]
        return InlineKeyboardMarkup(
            inline_keyboard=[
                choices[:2],
                choices[2:],
                [
                    InlineKeyboardButton(
                        text=self.text("bot.album_back", locale),
                        callback_data=encode_album_quality_back(request_id),
                    )
                ],
            ]
        )

    def batch_progress_text(
        self, title: str, progress: BatchProgress, locale: str, *, terminal: bool = False
    ) -> str:
        finished = progress.succeeded + progress.failed + progress.cancelled + progress.skipped
        if terminal and progress.succeeded == progress.total:
            return _bounded(f"{title}\n\n✓ {progress.total} / {progress.total} delivered", 4096)
        if terminal and progress.cancelled and progress.succeeded:
            return _bounded(
                f"{title}\n\n✓ {progress.succeeded} delivered\n"
                f"✗ {progress.cancelled + progress.failed + progress.skipped} "
                "cancelled/not completed",
                4096,
            )
        return _bounded(
            "\n\n".join(
                (
                    title,
                    f"{finished} / {progress.total} finished",
                    f"✓ {progress.succeeded} delivered",
                    f"✗ {progress.failed} failed",
                    f"↻ {progress.running + progress.delivering} active",
                    f"{progress.pending + progress.queued} queued",
                )
            ),
            4096,
        )

    def batch_progress_keyboard(
        self, locale: str, *, batch_id: int, retry: bool = False
    ) -> InlineKeyboardMarkup:
        callback = encode_batch_retry(batch_id) if retry else encode_batch_cancel(batch_id)
        key = "bot.batch_retry_failed" if retry else "bot.batch_cancel_remaining"
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=self.text(key, locale), callback_data=callback)]
            ]
        )

    def album_card_text(self, card: AlbumCard, locale: str, *, mode: str = "card") -> str:
        sections = [f"{card.artist} — {card.title}"]
        sections.append(self.text("bot.album_tracks", locale, count=card.track_count))
        if card.duration_ms is not None:
            sections.append(
                self.text("bot.album_duration", locale, duration=_format_duration(card.duration_ms))
            )
        if card.release_date:
            sections.append(self.text("bot.album_release_date", locale, date=card.release_date))
        if mode == "first_quality":
            sections.append(self.text("bot.album_choose_default_quality", locale))
        elif mode == "album_quality":
            sections.append(self.text("bot.album_choose_quality", locale))
            sections.append(self.text("bot.album_quality_one_off_hint", locale))
        elif card.quality_profile is not None:
            sections.append(
                self.text(
                    "bot.album_quality",
                    locale,
                    quality=self.quality_name(card.quality_profile, locale),
                )
            )
        return _bounded("\n\n".join(sections), 4096)

    def album_selection_text(self, page: AlbumSelectionPage, locale: str) -> str:
        return _bounded(
            "\n\n".join(
                [
                    f"{page.card.artist} — {page.card.title}",
                    self.text(
                        "bot.album_selected_count",
                        locale,
                        selected=page.selected_count,
                        total=page.card.track_count,
                    ),
                    self.text(
                        "bot.album_page",
                        locale,
                        page=page.page + 1,
                        pages=page.page_count,
                    ),
                ]
            ),
            4096,
        )

    def album_selection_keyboard(
        self, page: AlbumSelectionPage, locale: str
    ) -> InlineKeyboardMarkup:
        rows: list[list[InlineKeyboardButton]] = []
        for item in page.items:
            marker = "☑" if item.selected else "☐"
            number = (
                f"D{item.disc_number} · {item.track_number:02d}"
                if item.disc_number is not None and item.track_number is not None
                else f"{item.position:02d}"
            )
            title = item.title or self.text("bot.track_card_unknown_title", locale)
            rows.append(
                [
                    InlineKeyboardButton(
                        text=_bounded(f"{marker} {number}. {title}", 48),
                        callback_data=encode_album_toggle(page.card.request_id, item.id, page.page),
                    )
                ]
            )
        navigation: list[InlineKeyboardButton] = []
        if page.page > 0:
            navigation.append(
                InlineKeyboardButton(
                    text=self.text("bot.album_previous_page", locale),
                    callback_data=encode_album_page(page.card.request_id, page.page - 1),
                )
            )
        if page.page + 1 < page.page_count:
            navigation.append(
                InlineKeyboardButton(
                    text=self.text("bot.album_next_page", locale),
                    callback_data=encode_album_page(page.card.request_id, page.page + 1),
                )
            )
        if navigation:
            rows.append(navigation)
        rows.append(
            [
                InlineKeyboardButton(
                    text=self.text("bot.album_select_all", locale),
                    callback_data=encode_album_select_all(page.card.request_id),
                ),
                InlineKeyboardButton(
                    text=self.text("bot.album_clear_all", locale),
                    callback_data=encode_album_clear_all(page.card.request_id),
                ),
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text=self.text(
                        "bot.album_download_selected",
                        locale,
                        count=page.selected_count,
                    ),
                    callback_data=encode_album_download_selected(page.card.request_id),
                )
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text=self.text("bot.album_back", locale),
                    callback_data=encode_album_selection_back(page.card.request_id),
                )
            ]
        )
        return InlineKeyboardMarkup(inline_keyboard=rows)

    def track_card_text(self, card: TrackCard, locale: str, *, mode: str = "card") -> str:
        artist = card.artist or self.text("bot.track_card_unknown_artist", locale)
        title = card.title or self.text("bot.track_card_unknown_title", locale)
        sections = [f"{artist} — {title}"]
        if card.album:
            sections.append(self.text("bot.track_card_album", locale, album=card.album))
        if card.duration_ms is not None:
            sections.append(
                self.text(
                    "bot.track_card_duration",
                    locale,
                    duration=_format_duration(card.duration_ms),
                )
            )
        if mode == "first_quality":
            sections.append(self.text("bot.choose_default_quality", locale))
        elif mode == "track_quality":
            sections.append(self.text("bot.choose_track_quality", locale))
            sections.append(self.text("bot.track_quality_one_off_hint", locale))
        elif card.quality_profile is not None:
            sections.append(
                self.text(
                    "bot.track_card_default_quality",
                    locale,
                    quality=self.quality_name(card.quality_profile, locale),
                )
            )
        return _bounded("\n\n".join(sections), 4096)

    def language_keyboard(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="English", callback_data=encode_locale("en"))],
                [InlineKeyboardButton(text="Русский", callback_data=encode_locale("ru"))],
            ]
        )

    def quality_name(self, quality: QualityProfile | None, locale: str) -> str:
        if quality is None:
            return self.text("quality.not_selected", locale)
        return self.text(f"quality.{quality.value}.label", locale)


def _encode_request(prefix: str, request_id: int) -> str:
    if request_id <= 0:
        raise ValueError("invalid request ID")
    return f"{prefix}:{request_id}"


def _parse_request(value: str | None, prefix: str) -> int | None:
    if not value:
        return None
    parts = value.split(":")
    if len(parts) != 2 or parts[0] != prefix:
        return None
    return _positive_int(parts[1])


def _positive_int(value: str) -> int | None:
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _nonnegative_int(value: str) -> int | None:
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


def _encode_quality(prefix: str, request_id: int, quality: QualityProfile) -> str:
    if request_id <= 0:
        raise ValueError("invalid request ID")
    return f"{prefix}:{request_id}:{QUALITY_CODES[quality]}"


def _parse_album_quality(value: str | None, prefix: str) -> AlbumQualityCallback | None:
    if not value:
        return None
    parts = value.split(":")
    if len(parts) != 3 or parts[0] != prefix or parts[2] not in CODE_QUALITIES:
        return None
    request_id = _positive_int(parts[1])
    if request_id is None:
        return None
    return AlbumQualityCallback(request_id, CODE_QUALITIES[parts[2]])


def _format_duration(duration_ms: int) -> str:
    total_seconds = max(duration_ms, 0) // 1000
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def _bounded(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"
