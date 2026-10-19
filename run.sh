#!/usr/bin/env bash
#
# run.sh — start memry locally for review.
#
# AWM contract (used by the Developer-review "Run app" button):
#   --port-offset N    shift EVERY host port this app binds (servers AND any
#                      docker-compose published ports) so a branch worktree can
#                      run beside the main checkout's stack
#   --frontend-port P  explicit override, wins over the offset
#   --backend-port P   explicit override, wins over the offset
#
# It also announces its URLs so AWM's "Open app" targets the UI rather than
# guessing from the log:
#   [awm] url frontend=http://localhost:<port>
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

DEFAULT_FRONTEND_PORT=8787
DEFAULT_BACKEND_PORT=8787

OFFSET=0
FRONTEND_PORT=""
BACKEND_PORT=""

while [ $# -gt 0 ]; do
  case "$1" in
    --port-offset)   shift; OFFSET="${1:?--port-offset needs a value}" ;;
    --frontend-port) shift; FRONTEND_PORT="${1:?--frontend-port needs a value}" ;;
    --backend-port)  shift; BACKEND_PORT="${1:?--backend-port needs a value}" ;;
    -h|--help)       sed -n '2,18p' "$0"; exit 0 ;;
    *) echo "run.sh: unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

[ -n "$FRONTEND_PORT" ] || FRONTEND_PORT=$((DEFAULT_FRONTEND_PORT + OFFSET))
[ -n "$BACKEND_PORT" ]  || BACKEND_PORT=$((DEFAULT_BACKEND_PORT + OFFSET))

# One uvicorn process serves the dashboard, the REST API and /mcp on a single
# port, so both port settings name the same socket: an explicit
# --frontend-port wins (it is what AWM opens), else --backend-port, else the
# offset default.
if [ "$FRONTEND_PORT" = "$((DEFAULT_FRONTEND_PORT + OFFSET))" ]; then
  FRONTEND_PORT="$BACKEND_PORT"
fi
PORT="$FRONTEND_PORT"
BACKEND_PORT="$PORT"

# A branch worktree has neither .env nor .venv: fall back to the main checkout's.
MAIN_ROOT="$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")"
for ENV_FILE in .env "$MAIN_ROOT/.env"; do
  if [ -f "$ENV_FILE" ]; then set -a; . "$ENV_FILE"; set +a; break; fi
done
PY=.venv/bin/python
[ -x "$PY" ] || PY="$MAIN_ROOT/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"

# Isolated state per port: never the developer's ~/.memry database or config,
# and no nightly snapshot. The login database lands beside memry.db.
RUN_DIR="$PWD/.memry/run-$PORT"
mkdir -p "$RUN_DIR"
export MEMRY_DB_PATH="$RUN_DIR/memry.db"
export MEMRY_CONFIG="$RUN_DIR/config.json"
unset MEMRY_AUTH_DB_PATH MEMRY_SNAPSHOT_DIR
# This checkout's source wins over the editable install the venv points at.
export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"

echo "[awm] url frontend=http://localhost:$FRONTEND_PORT"
echo "[awm] url api=http://localhost:$PORT/api/v1"
echo "[awm] url mcp=http://localhost:$PORT/mcp"

# `exec` so this script's pid leads the process group — that is what lets AWM
# tear the whole tree down, and what keeps its pid identifiable afterwards.
exec "$PY" -m memry.cli serve --host 127.0.0.1 --port "$PORT"
