"""Private JSON Lines protocol shared by the OnTheSpot parent and worker."""

from __future__ import annotations

from typing import Final

MAX_MESSAGE_BYTES: Final = 1024 * 1024
DEFAULT_REQUEST_TIMEOUT_SECONDS: Final = 60.0
PROTOCOL_VERSION: Final = 1

INITIALIZE_METHOD: Final = "initialize"
GET_METADATA_METHOD: Final = "get_metadata"
SHUTDOWN_METHOD: Final = "shutdown"
