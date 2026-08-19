"""Local operator CLI for the same ephemeral Provider Health service."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence

from app.providers.onthespot import OnTheSpotProvider
from app.services.provider_health import ProviderHealthService


def _parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        description="Inspect live provider-level download readiness without downloading media."
    )


async def _run() -> None:
    provider = OnTheSpotProvider()
    service = ProviderHealthService.for_local_operator(provider)
    try:
        snapshot = await service.check_all_local()
        print("Provider Health")
        print(f"Checked: {snapshot.checked_at.isoformat()}")
        for entry in snapshot.entries:
            suffix = f" ({entry.error_code.value})" if entry.error_code else ""
            print(f"{entry.provider.value}: {entry.status.value}{suffix}")
    finally:
        await provider.close()


def main(argv: Sequence[str] | None = None) -> int:
    _parser().parse_args(argv)
    asyncio.run(_run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
