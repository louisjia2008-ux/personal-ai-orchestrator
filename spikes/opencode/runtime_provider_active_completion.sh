#!/usr/bin/env bash
# Stage C provider-native SHADOW + ACTIVE + real repository-read completion proof.
#
# All writes occur in a disposable Git fixture. OpenCode owns provider credentials;
# this harness never reads, copies, logs, or persists credential contents.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

OPENCODE_BIN="opencode"
PYTHON_BIN=""
PROVIDER=""
MODEL=""
DAEMON_PORT="8791"
EVIDENCE_OUT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --provider) PROVIDER="$2"; shift 2 ;;
    --model) MODEL="$2"; shift 2 ;;
    --opencode) OPENCODE_BIN="$2"; shift 2 ;;
    --python) PYTHON_BIN="$2"; shift 2 ;;
    --daemon-port) DAEMON_PORT="$2"; shift 2 ;;
    --evidence-out) EVIDENCE_OUT="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 64 ;;
  esac
done
if [ -z "$PROVIDER" ] || [ -z "$MODEL" ]; then
  echo "usage: $0 --provider <id> --model <id> [--opencode <bin>] [--python <python>]" >&2
  exit 64
fi
if [ -z "$PYTHON_BIN" ]; then
  if [ -x "$REPO_ROOT/.venv/bin/python" ]; then
    PYTHON_BIN="$REPO_ROOT/.venv/bin/python"
  else
    PYTHON_BIN="python3"
  fi
fi

bash "$SCRIPT_DIR/runtime_provider_auth_smoke.sh" \
  --provider "$PROVIDER" --model "$MODEL" --opencode "$OPENCODE_BIN"

fixture="$(mktemp -d)"
workdir="$(mktemp -d)"
serve_log="$workdir/opencode-serve.log"
daemon_log="$workdir/fake-daemon.log"
serve_pid=""
daemon_pid=""
cleanup_done=0

stop_exact_pid() {
  local pid="$1"
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
  fi
}

cleanup() {
  if [ "$cleanup_done" -eq 1 ]; then
    return
  fi
  stop_exact_pid "$daemon_pid"
  stop_exact_pid "$serve_pid"
  rm -rf "$fixture" "$workdir"
}
trap cleanup EXIT

git -C "$fixture" init -q
git -C "$fixture" config user.email "spike@example.invalid"
git -C "$fixture" config user.name "OpenCode Stage C"
nonce="$("$PYTHON_BIN" -c 'import secrets; print(secrets.token_hex(3).upper())')"
heading="# PAO Stage C Fixture $nonce"
printf '%s\n\nThis repository exists only for runtime validation.\n' "$heading" > "$fixture/README.md"
git -C "$fixture" add README.md
git -C "$fixture" commit -q -m "fixture: initialize"

"$PYTHON_BIN" "$SCRIPT_DIR/fake_daemon.py" \
  --host 127.0.0.1 --port "$DAEMON_PORT" --model "$PROVIDER/$MODEL" > "$daemon_log" 2>&1 &
daemon_pid=$!
for _ in $(seq 1 40); do
  grep -q 'fake orchestrator listening' "$daemon_log" && break
  kill -0 "$daemon_pid" 2>/dev/null || { echo "daemon failed to start"; exit 1; }
  sleep 0.25
done
grep -q 'fake orchestrator listening' "$daemon_log"
DAEMON_URL="http://127.0.0.1:$DAEMON_PORT"

# Launch directly so $! is the actual OpenCode server PID, not a subshell PID.
pushd "$fixture" >/dev/null
"$OPENCODE_BIN" serve --port 0 --hostname 127.0.0.1 > "$serve_log" 2>&1 &
serve_pid=$!
popd >/dev/null
SERVER_URL=""
for _ in $(seq 1 60); do
  SERVER_URL="$(grep -oE 'http://127\.0\.0\.1:[0-9]+' "$serve_log" | head -n1 || true)"
  [ -n "$SERVER_URL" ] && break
  kill -0 "$serve_pid" 2>/dev/null || { echo "serve failed to start"; exit 1; }
  sleep 0.5
done
[ -n "$SERVER_URL" ] || { echo "could not determine server url"; exit 1; }
for _ in $(seq 1 40); do
  curl -sf "$SERVER_URL/api/health" >/dev/null 2>&1 && break
  sleep 0.5
done

LOG_DIR="$("$OPENCODE_BIN" debug paths 2>/dev/null | awk '$1=="log"{print $2}')"
OPENCODE_LOG="${LOG_DIR:-$HOME/.local/share/opencode/log}/opencode.log"

echo "STAGE_C provider=$PROVIDER model=$MODEL"
echo "STAGE_C serve_pid=$serve_pid daemon_pid=$daemon_pid"

set +e
if [ -n "$EVIDENCE_OUT" ]; then
  "$PYTHON_BIN" "$SCRIPT_DIR/stage_c_runtime.py" \
    --server-url "$SERVER_URL" \
    --daemon-url "$DAEMON_URL" \
    --directory "$fixture" \
    --provider "$PROVIDER" \
    --model "$MODEL" \
    --opencode-log "$OPENCODE_LOG" \
    --evidence-out "$EVIDENCE_OUT"
else
  "$PYTHON_BIN" "$SCRIPT_DIR/stage_c_runtime.py" \
    --server-url "$SERVER_URL" \
    --daemon-url "$DAEMON_URL" \
    --directory "$fixture" \
    --provider "$PROVIDER" \
    --model "$MODEL" \
    --opencode-log "$OPENCODE_LOG"
fi
driver_status=$?
set -e

fixture_status="$(git -C "$fixture" status --short)"
git -C "$fixture" diff --check
if [ -n "$fixture_status" ]; then
  echo "FIXTURE MODIFIED (must stay clean)"
  echo "$fixture_status"
  exit 1
fi
echo "STAGE_C fixture_clean=YES"

active_count="$(grep -c '"mode": "ACTIVE"' "$daemon_log" || true)"
echo "STAGE_C active_route_count=$active_count"

# Explicitly stop and verify the exact processes before removing the fixture.
stop_exact_pid "$daemon_pid"
stop_exact_pid "$serve_pid"
if kill -0 "$daemon_pid" 2>/dev/null; then
  echo "STAGE_C daemon_cleanup=FAIL pid=$daemon_pid"
  exit 1
fi
if kill -0 "$serve_pid" 2>/dev/null; then
  echo "STAGE_C serve_cleanup=FAIL pid=$serve_pid"
  exit 1
fi
echo "STAGE_C daemon_cleanup=PASS exact_pid=$daemon_pid"
echo "STAGE_C serve_cleanup=PASS exact_pid=$serve_pid"

rm -rf "$fixture" "$workdir"
if [ -e "$fixture" ] || [ -e "$workdir" ]; then
  echo "STAGE_C fixture_cleanup=FAIL"
  exit 1
fi
echo "STAGE_C fixture_deleted=YES"
cleanup_done=1
trap - EXIT

exit "$driver_status"
