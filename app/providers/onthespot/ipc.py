"""Private JSON Lines protocol shared by the OnTheSpot parent and worker."""

from __future__ import annotations

from typing import Final

MAX_MESSAGE_BYTES: Final = 1024 * 1024
DEFAULT_REQUEST_TIMEOUT_SECONDS: Final = 60.0
PROTOCOL_VERSION: Final = 1

INITIALIZE_METHOD: Final = "initialize"
GET_METADATA_METHOD: Final = "get_metadata"
GET_TRACK_METADATA_METHOD: Final = "get_track_metadata"
MATCH_URL_METHOD: Final = "match_url"
RESOLVE_ALBUM_METHOD: Final = "resolve_album"
RESOLVE_ALBUM_ID_METHOD: Final = "resolve_album_id"
LIST_SEARCHABLE_PROVIDERS_METHOD: Final = "list_searchable_providers"
SEARCH_TRACKS_METHOD: Final = "search_tracks"
CHECK_SOURCE_METHOD: Final = "check_source"
CHECK_PROVIDER_HEALTH_METHOD: Final = "check_provider_health"
REFRESH_PROVIDER_HEALTH_METHOD: Final = "refresh_provider_health"
RECONCILE_PROVIDER_LIFECYCLE_METHOD: Final = "reconcile_provider_lifecycle"
RESET_PROVIDER_AUTHENTICATION_METHOD: Final = "reset_provider_authentication"
DEEZER_ARL_AUTHORIZE_METHOD: Final = "deezer_arl_authorize"
SPOTIFY_COMPONENT_STATUS_METHOD: Final = "spotify_component_status"
SPOTIFY_PLAYBACK_PAIRING_START_METHOD: Final = "spotify_playback_pairing_start"
SPOTIFY_PLAYBACK_PAIRING_POLL_METHOD: Final = "spotify_playback_pairing_poll"
SPOTIFY_PLAYBACK_PAIRING_CANCEL_METHOD: Final = "spotify_playback_pairing_cancel"
SPOTIFY_WEBAPI_AUTHORIZE_METHOD: Final = "spotify_webapi_authorize"
TIDAL_DEVICE_AUTHORIZATION_START_METHOD: Final = "tidal_device_authorization_start"
TIDAL_DEVICE_AUTHORIZATION_POLL_METHOD: Final = "tidal_device_authorization_poll"
TIDAL_DEVICE_AUTHORIZATION_CANCEL_METHOD: Final = "tidal_device_authorization_cancel"
PREPARE_SOURCE_METHOD: Final = "prepare_source"
DOWNLOAD_NATIVE_METHOD: Final = "download_native"
SHUTDOWN_METHOD: Final = "shutdown"
