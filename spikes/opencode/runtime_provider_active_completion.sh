#!/usr/bin/env bash
# Stage C provider-native SHADOW + ACTIVE + real-completion runtime proof.
#
# Ties together, against a REAL authenticated OpenCode provider:
#   - a disposable git fixture repository (no real user project is ever touched);
#   - a real headless `opencode serve` runtime that owns provider credentials;
#   - the committed credential-free fake routing daemon (the orchestrator stand-in);
#   - the committed thin-adapter decision logic (`resolve_adapter_outcome`) driving
#     a session-scoped model switch over OpenCode's documented HTTP API;
#   - one minimal, harmless real completion, plus second-session isolation and a
#     bounded cancellation smoke.
#
# Credentials are never read, copied, logged, or transmitted by this harness or
# the orchestrator. OpenCode owns authentication; only sanitized contract/usage
# metadata is emitted.
#
# Usage:
#   runtime_provider_active_completion.sh --provider <id> --model <id> \
#     [--opencode <bin>] [--python <python>] [--daemon-port <port>] \
#     [--evidence-out <file>]
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
  if [ -x "$REPO_ROOT/.venv/bin/python" ]; then PYTHON_BIN="$REPO_ROOT/.venv/bin/python"; else PYTHON_BIN="python3"; fi
fi

# --- Preflight: refuse unless the provider is really authenticated. -----------
bash "$SCRIPT_DIR/runtime_provider_auth_smoke.sh" \
  --provider "$PROVIDER" --model "$MODEL" --opencode "$OPENCODE_BIN"

fixture="$(mktemp -d)"
# Runtime logs live OUTSIDE the fixture so the disposable repo stays byte-clean.
workdir="$(mktemp -d)"
serve_log="$workdir/opencode-serve.log"
daemon_log="$workdir/fake-daemon.log"
serve_pid=""
daemon_pid=""

cleanup() {
  if [ -n "$daemon_pid" ] && kill -0 "$daemon_pid" 2>/dev/null; then
    kill "$daemon_pid" 2>/dev/null || true
    wait "$daemon_pid" 2>/dev/null || true
  fi
  if [ -n "$serve_pid" ] && kill -0 "$serve_pid" 2>/dev/null; then
    kill "$serve_pid" 2>/dev/null || true
    wait "$serve_pid" 2>/dev/null || true
  fi
  rm -rf "$fixture" "$workdir"
}
trap cleanup EXIT

# --- Disposable fixture repository. -------------------------------------------
git -C "$fixture" init -q
git -C "$fixture" config user.email "spike@example.invalid"
git -C "$fixture" config user.name "OpenCode Stage C"
printf '# OpenCode Stage C Disposable Fixture\n\nThis repository exists only for provider-native routing validation.\n' \
  > "$fixture/README.md"
git -C "$fixture" add README.md
git -C "$fixture" commit -q -m "fixture: initialize"

# --- Credential-free routing daemon (the orchestrator stand-in). --------------
"$PYTHON_BIN" "$SCRIPT_DIR/fake_daemon.py" \
  --host 127.0.0.1 --port "$DAEMON_PORT" --model "$PROVIDER/$MODEL" > "$daemon_log" 2>&1 &
daemon_pid=$!
for _ in $(seq 1 40); do
  grep -q 'fake orchestrator listening' "$daemon_log" && break
  kill -0 "$daemon_pid" 2>/dev/null || { echo "daemon failed to start"; cat "$daemon_log"; exit 1; }
  sleep 0.25
done
grep -q 'fake orchestrator listening' "$daemon_log"
DAEMON_URL="http://127.0.0.1:$DAEMON_PORT"

# --- Real headless OpenCode runtime (owns provider credentials). --------------
# Launch without a subshell so $! is the real opencode PID (a subshell's PID
# would leave the server orphaned when the cleanup trap fires). pushd/popd keeps
# the caller's cwd intact for any relative --evidence-out path.
pushd "$fixture" >/dev/null
"$OPENCODE_BIN" serve --port 0 --hostname 127.0.0.1 > "$serve_log" 2>&1 &
serve_pid=$!
popd >/dev/null
SERVER_URL=""
for _ in $(seq 1 60); do
  SERVER_URL="$(grep -oE 'http://127\.0\.0\.1:[0-9]+' "$serve_log" | head -n1 || true)"
  [ -n "$SERVER_URL" ] && break
  kill -0 "$serve_pid" 2>/dev/null || { echo "serve failed to start"; cat "$serve_log"; exit 1; }
  sleep 0.5
done
[ -n "$SERVER_URL" ] || { echo "could not determine server url"; cat "$serve_log"; exit 1; }
for _ in $(seq 1 40); do
  curl -sf "$SERVER_URL/api/health" >/dev/null 2>&1 && break
  sleep 0.5
done

LOG_DIR="$("$OPENCODE_BIN" debug paths 2>/dev/null | awk '$1=="log"{print $2}')"
OPENCODE_LOG="${LOG_DIR:-$HOME/.local/share/opencode/log}/opencode.log"

echo "STAGE_C server=$SERVER_URL daemon=$DAEMON_URL provider=$PROVIDER model=$MODEL fixture=$fixture"

# --- Drive the contract: SHADOW -> ACTIVE -> completion -> isolation -> cancel.-
set +e
"$PYTHON_BIN" "$SCRIPT_DIR/stage_c_runtime.py" \
  --server-url "$SERVER_URL" \
  --daemon-url "$DAEMON_URL" \
  --directory "$fixture" \
  --provider "$PROVIDER" \
  --model "$MODEL" \
  --opencode-log "$OPENCODE_LOG" \
  ${EVIDENCE_OUT:+--evidence-out "$EVIDENCE_OUT"}
driver_status=$?
set -e

# --- The disposable fixture must be byte-for-byte unchanged. ------------------
fixture_status="$(git -C "$fixture" status --short)"
git -C "$fixture" diff --check
if [ -n "$fixture_status" ]; then
  echo "FIXTURE MODIFIED (must stay clean):"
  echo "$fixture_status"
  exit 1
fi
echo "STAGE_C fixture_clean=YES"

echo '--- routing daemon evidence (credential-free contract metadata only) ---'
cat "$daemon_log"
active_count="$(grep -c '"mode": "ACTIVE"' "$daemon_log" || true)"
echo "STAGE_C active_route_count=$active_count"

exit "$driver_status"
