from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_release_smoke_is_fail_fast_scoped_and_noninteractive() -> None:
    script = (ROOT / "scripts" / "validate-production.sh").read_text(encoding="utf-8")

    assert "set -euo pipefail" in script
    assert "docker image inspect" in script
    assert "docker compose config --quiet" in script
    assert "alembic upgrade head" in script
    assert "ScriptDirectory.from_config" in script
    assert "expected one Alembic head" in script
    assert "IMAGE_ALEMBIC_HEAD" in script
    assert "alembic downgrade 20260820_0010" in script
    assert "python -m app.main --check" in script
    assert "python -m app.tools.ops backup create" in script
    assert "out-of-date schema unexpectedly passed" in script
    assert "onthespot_version=" in script
    assert "--read-only" in script
    assert "--signal KILL" in script
    assert "pytest" in script and "not external" in script
    assert "STAGE12_4_CONTAINER_VALIDATION=PASS" in script
    assert "MSYS2_ARG_CONV_EXCL='*'" in script
    assert "cygpath -w" in script
    assert "read -" not in script
    assert "docker volume prune" not in script
    assert "docker system prune" not in script
    assert "20260825_0012" not in script


def test_linux_ci_runs_host_and_production_image_gates_without_publishing() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release-validation.yml").read_text(
        encoding="utf-8"
    )

    assert "runs-on: ubuntu-latest" in workflow
    assert "uv lock --check" in workflow
    assert "uv sync --locked --extra dev --extra onthespot" in workflow
    assert 'uv run pytest -m "not external" -ra' in workflow
    assert "docker build" in workflow
    assert "--target validation" in workflow
    assert "bash scripts/validate-production.sh" in workflow
    assert "docker push" not in workflow
    assert "pytest -m external" not in workflow


def test_release_checklist_separates_mandatory_and_external_and_covers_licenses() -> None:
    checklist = (ROOT / "docs" / "release-checklist.md").read_text(encoding="utf-8")

    assert "## Mandatory" in checklist
    assert "## Optional external" in checklist
    assert "READY WITH EXTERNAL VERIFICATION PENDING" in checklist
    assert "NOT READY" in checklist
    assert "Linux/container" in checklist
    assert "OnTheSpot" in checklist
    assert "GPL" in checklist
    assert "not legal advice" in checklist
