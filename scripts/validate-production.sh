#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

IMAGE="${MUSICBOT_IMAGE:-musicbot-downloader:stage12.4}"
VALIDATION_IMAGE="${MUSICBOT_VALIDATION_IMAGE:-musicbot-downloader:stage12.4-validation}"
RUN_ID="${GITHUB_RUN_ID:-local}-$$"
DATA_VOLUME="musicbot-stage124-data-${RUN_ID}"
UPGRADE_VOLUME="musicbot-stage124-upgrade-${RUN_ID}"
LOCK_CONTAINER="musicbot-stage124-lock-${RUN_ID}"
WRITER_CONTAINER="musicbot-stage124-writer-${RUN_ID}"
FIXTURE_PATH="$ROOT_DIR/scripts/container_fixture.py"

# The working tree and production image must agree on exactly one Alembic
# head.  Deriving this through Alembic keeps future migrations covered and
# fails closed if the repository ever grows a branch in its migration graph.
EXPECTED_ALEMBIC_HEAD="$(uv run python -c "from alembic.config import Config; from alembic.script import ScriptDirectory; heads=ScriptDirectory.from_config(Config('alembic.ini')).get_heads(); assert len(heads) == 1, f'expected one Alembic head, got {heads!r}'; print(heads[0])")"

# Git Bash/MSYS rewrites Linux container paths (for example /tmp/musicbot)
# before passing them to the Windows Docker CLI. Disable that rewriting for
# Docker arguments, while giving Docker the one host bind mount in native form.
if [[ -n "${MSYSTEM:-}" ]] && command -v cygpath >/dev/null 2>&1; then
  FIXTURE_PATH="$(cygpath -w "$FIXTURE_PATH")"
  export MSYS2_ARG_CONV_EXCL='*'
fi
FIXTURE_MOUNT="$FIXTURE_PATH:/validation/container_fixture.py:ro"

cleanup() {
  docker rm --force "$LOCK_CONTAINER" "$WRITER_CONTAINER" >/dev/null 2>&1 || true
  docker volume rm "$DATA_VOLUME" "$UPGRADE_VOLUME" >/dev/null 2>&1 || true
}
trap cleanup EXIT

if [[ ! -f .env ]]; then
  echo "release smoke requires a local .env (a fake CI fixture is sufficient)" >&2
  exit 2
fi

docker image inspect "$IMAGE" --format 'image={{.Id}} platform={{.Os}}/{{.Architecture}} size_bytes={{.Size}}'
docker image inspect "$VALIDATION_IMAGE" >/dev/null
IMAGE_ALEMBIC_HEAD="$(docker run --rm --entrypoint python "$IMAGE" -c "from alembic.config import Config; from alembic.script import ScriptDirectory; heads=ScriptDirectory.from_config(Config('alembic.ini')).get_heads(); assert len(heads) == 1, f'expected one Alembic head, got {heads!r}'; print(heads[0])")"
if [[ "$IMAGE_ALEMBIC_HEAD" != "$EXPECTED_ALEMBIC_HEAD" ]]; then
  echo "production image Alembic head $IMAGE_ALEMBIC_HEAD does not match repository head $EXPECTED_ALEMBIC_HEAD" >&2
  exit 1
fi
echo "alembic_expected_head=$EXPECTED_ALEMBIC_HEAD"
docker compose config --quiet
docker volume create "$DATA_VOLUME" >/dev/null
docker volume create "$UPGRADE_VOLUME" >/dev/null

common=(
  --env DATABASE_URL=sqlite+aiosqlite:////data/musicbot.db
  --env TEMP_DIR=/tmp/musicbot
  --env TEMP_DISK_MIN_FREE_BYTES=0
  --env BOT_TOKEN=123456:STAGE124_FAKE_TOKEN
  --env TELEGRAM_CACHE_CHAT_ID=-1001204
)

run_data() {
  docker run --rm "${common[@]}" --volume "$DATA_VOLUME:/data" "$IMAGE" "$@"
}

run_fixture() {
  docker run --rm "${common[@]}" --volume "$DATA_VOLUME:/data" \
    --volume "$FIXTURE_MOUNT" --entrypoint python "$IMAGE" \
    /validation/container_fixture.py "$1"
}

echo '== production image executables and identity =='
docker run --rm --entrypoint python "$IMAGE" --version
docker run --rm --entrypoint ffmpeg "$IMAGE" -version
docker run --rm --entrypoint ffprobe "$IMAGE" -version
docker run --rm --entrypoint id "$IMAGE"
[[ "$(docker run --rm --entrypoint id "$IMAGE" -u)" == "10001" ]]
[[ "$(docker run --rm --entrypoint id "$IMAGE" -g)" == "10001" ]]
docker run --rm --entrypoint python "$IMAGE" -m app.main --help
docker run --rm --entrypoint python "$IMAGE" -m app.tools.ops --help
docker run --rm --entrypoint python "$IMAGE" -c \
  "import importlib.util as u; assert all(u.find_spec(n) is None for n in ('pytest','ruff','mypy'))"
docker run --rm --entrypoint sh "$IMAGE" -ec \
  'for executable in uv git gcc cc make pytest ruff mypy; do ! command -v "$executable" >/dev/null 2>&1; done; test ! -e /app/.env; test ! -e /app/tests; test ! -e /app/musicbot.db; test -d /app/alembic; test -f /app/alembic.ini; test -f /app/app/i18n/locales/en/messages.json; test -f /app/app/i18n/locales/ru/messages.json'
docker run --rm --entrypoint python "$IMAGE" -c \
  "from importlib.metadata import distribution; d=distribution('onthespot'); files=[p for p in d.files or () if str(p).endswith('dist-info/licenses/LICENSE')]; assert d.version == '0.1.0' and files; print(f'onthespot_version={d.version} license={files[0]}')"

echo '== fresh database and read-only-root preflight =='
run_data alembic upgrade head
run_data alembic current
run_data alembic heads
run_data alembic check
run_data python -m app.main --check
run_data python -m app.tools.ops status --json
docker run --rm --read-only \
  --tmpfs /tmp/musicbot:rw,noexec,nosuid,nodev,uid=10001,gid=10001,mode=0700 \
  "${common[@]}" --volume "$DATA_VOLUME:/data" "$IMAGE" python -m app.main --check

echo '== controlled writable paths =='
run_data sh -ec \
  "test ! -w /app; test ! -w /root; mkdir -p /tmp/musicbot/check /data/onthespot /data/xdg-config; printf ok > /tmp/musicbot/check/a; mv /tmp/musicbot/check/a /tmp/musicbot/check/b; rm /tmp/musicbot/check/b; rmdir /tmp/musicbot/check; printf ok > /data/onthespot/.stage124; printf ok > /data/xdg-config/.stage124; rm /data/onthespot/.stage124 /data/xdg-config/.stage124"

echo '== existing database 0010 -> current head and downgrade/re-upgrade =='
docker run --rm "${common[@]}" --volume "$UPGRADE_VOLUME:/data" "$IMAGE" \
  alembic upgrade 20260820_0010
docker run --rm "${common[@]}" --volume "$UPGRADE_VOLUME:/data" \
  --volume "$FIXTURE_MOUNT" --entrypoint python "$IMAGE" \
  /validation/container_fixture.py seed-preupgrade
if docker run --rm "${common[@]}" --volume "$UPGRADE_VOLUME:/data" "$IMAGE" \
  python -m app.main --check; then
  echo 'out-of-date schema unexpectedly passed production preflight' >&2
  exit 1
fi
docker run --rm "${common[@]}" --volume "$UPGRADE_VOLUME:/data" "$IMAGE" alembic upgrade head
docker run --rm "${common[@]}" --volume "$UPGRADE_VOLUME:/data" \
  --volume "$FIXTURE_MOUNT" --entrypoint python "$IMAGE" \
  /validation/container_fixture.py verify-preupgrade
docker run --rm "${common[@]}" --volume "$UPGRADE_VOLUME:/data" "$IMAGE" \
  alembic downgrade 20260820_0010
docker run --rm "${common[@]}" --volume "$UPGRADE_VOLUME:/data" "$IMAGE" alembic upgrade head
docker run --rm "${common[@]}" --volume "$UPGRADE_VOLUME:/data" "$IMAGE" alembic check

echo '== persistent durable state across container recreation =='
run_fixture seed-preupgrade
run_fixture seed-durable
run_fixture verify-durable
run_fixture verify-durable

echo '== live WAL backup, offline-lock enforcement, and restore drill =='
docker run --detach --name "$WRITER_CONTAINER" "${common[@]}" \
  --volume "$DATA_VOLUME:/data" --volume "$FIXTURE_MOUNT" \
  --entrypoint python "$IMAGE" /validation/container_fixture.py write-and-hold-lock >/dev/null
for _ in {1..50}; do
  docker logs "$WRITER_CONTAINER" 2>&1 | grep -q '^READY$' && break
  sleep 0.1
done
docker logs "$WRITER_CONTAINER" 2>&1 | grep -q '^READY$'
run_data python -m app.tools.ops status --json
if run_data python -m app.tools.ops recovery inspect; then
  echo 'offline recovery unexpectedly succeeded while runtime lock was active' >&2
  exit 1
fi
run_data python -m app.tools.ops backup create /data/release-backup.db --json
[[ "$(run_data stat -c '%a' /data/release-backup.db)" == "600" ]]
run_data python -c \
  "import sqlite3; c=sqlite3.connect('/data/release-backup.db'); assert c.execute('pragma integrity_check').fetchone()==('ok',); assert c.execute('select version_num from alembic_version').fetchone()==('$EXPECTED_ALEMBIC_HEAD',)"
docker stop --time 10 "$WRITER_CONTAINER" >/dev/null
docker rm "$WRITER_CONTAINER" >/dev/null
run_fixture mutate
run_data sh -ec \
  "cp /data/musicbot.db /data/post-backup-original.db; if test -e /data/musicbot.db-wal; then cp /data/musicbot.db-wal /data/post-backup-original.db-wal; rm -- /data/musicbot.db-wal; fi; if test -e /data/musicbot.db-shm; then cp /data/musicbot.db-shm /data/post-backup-original.db-shm; rm -- /data/musicbot.db-shm; fi; cp /data/release-backup.db /data/restore.partial; chmod 600 /data/restore.partial; mv /data/restore.partial /data/musicbot.db"
run_data stat -c '%u:%g %a' /data/musicbot.db
run_data alembic current
run_data alembic check
run_data python -m app.main --check
run_data python -m app.tools.ops status --json
run_fixture verify-restored

echo '== POSIX OS-lock behavior =='
docker run --detach --name "$LOCK_CONTAINER" "${common[@]}" \
  --volume "$DATA_VOLUME:/data" --volume "$FIXTURE_MOUNT" \
  --entrypoint python "$IMAGE" /validation/container_fixture.py hold-lock >/dev/null
for _ in {1..50}; do
  docker logs "$LOCK_CONTAINER" 2>&1 | grep -q '^READY$' && break
  sleep 0.1
done
docker logs "$LOCK_CONTAINER" 2>&1 | grep -q '^READY$'
if run_fixture probe-lock; then
  echo 'second lock holder unexpectedly succeeded' >&2
  exit 1
fi
docker stop --time 10 "$LOCK_CONTAINER" >/dev/null
docker rm "$LOCK_CONTAINER" >/dev/null
run_fixture probe-lock
docker run --detach --name "$LOCK_CONTAINER" "${common[@]}" \
  --volume "$DATA_VOLUME:/data" --volume "$FIXTURE_MOUNT" \
  --entrypoint python "$IMAGE" /validation/container_fixture.py hold-lock >/dev/null
for _ in {1..50}; do
  docker logs "$LOCK_CONTAINER" 2>&1 | grep -q '^READY$' && break
  sleep 0.1
done
docker kill --signal KILL "$LOCK_CONTAINER" >/dev/null
docker rm "$LOCK_CONTAINER" >/dev/null
run_fixture probe-lock

echo '== Linux regression suite in production-derived validation image =='
docker run --rm --entrypoint pytest "$VALIDATION_IMAGE" \
  -m 'not external' -p no:cacheprovider --basetemp=/tmp/musicbot/stage124-pytest -ra

echo 'STAGE12_4_CONTAINER_VALIDATION=PASS'
