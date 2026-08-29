#!/usr/bin/env bash
set -euo pipefail

fixture="$(mktemp -d)"
daemon_log="$fixture/fake-daemon.log"
daemon_pid=""

mkdir -p "$fixture/xdg" "$fixture/plugins"
git -C "$fixture" init -q
git -C "$fixture" config user.email "spike@example.invalid"
git -C "$fixture" config user.name "OpenCode Spike"
printf '# disposable OpenCode ACTIVE fixture\n' > "$fixture/README.md"
git -C "$fixture" add README.md
git -C "$fixture" commit -q -m "fixture: initialize"

cp "$GITHUB_WORKSPACE/integrations/opencode/plugin.ts" \
  "$fixture/plugins/orchestrator.ts"
cp "$GITHUB_WORKSPACE/integrations/opencode/decision_contract.ts" \
  "$fixture/plugins/decision_contract.ts"
cat > "$fixture/package.json" <<'EOF'
{
  "private": true,
  "type": "module",
  "dependencies": {
    "@opencode-ai/plugin": "0.0.0-beta-18387"
  }
}
EOF
npm install --prefix "$fixture"

cat > "$fixture/opencode.json" <<'EOF'
{
  "plugins": [
    {
      "package": "./plugins/orchestrator.ts",
      "options": {
        "endpoint": "http://127.0.0.1:8765",
        "mode": "ACTIVE"
      }
    }
  ]
}
EOF

export XDG_CONFIG_HOME="$fixture/xdg"
export OPENCODE_DB="$fixture/opencode.db"

cleanup() {
  if [ -n "$daemon_pid" ] && kill -0 "$daemon_pid" 2>/dev/null; then
    kill "$daemon_pid"
    wait "$daemon_pid" || true
  fi
  (cd "$fixture" && opencode2 service stop >/dev/null 2>&1) || true
  rm -rf "$fixture"
}
trap cleanup EXIT

python "$GITHUB_WORKSPACE/spikes/opencode/fake_daemon.py" \
  --model spike-provider/target-model > "$daemon_log" 2>&1 &
daemon_pid=$!

for attempt in $(seq 1 20); do
  if grep -q 'fake orchestrator listening' "$daemon_log"; then
    break
  fi
  if ! kill -0 "$daemon_pid" 2>/dev/null; then
    cat "$daemon_log"
    exit 1
  fi
  sleep 0.25
done
grep -q 'fake orchestrator listening' "$daemon_log"

cd "$fixture"

command_ready=0
for attempt in $(seq 1 25); do
  timeout 15 opencode2 api get /api/command \
    --header "x-opencode-directory: $fixture" > commands.json || true
  if grep -q 'orchestrator-route' commands.json; then
    command_ready=1
    break
  fi
  sleep 1
done
if [ "$command_ready" -ne 1 ]; then
  cat commands.json || true
  tail -n 300 "$HOME/.local/share/opencode/log/opencode.log" || true
  exit 1
fi

session_payload="$(python -c \
  'import json,sys; print(json.dumps({"location":{"directory":sys.argv[1]}}))' \
  "$fixture")"

create_session() {
  local output="$1"
  timeout 30 opencode2 api post /api/session \
    --header "x-opencode-directory: $fixture" \
    --data "$session_payload" > "$output"
}

create_session session-a-created.json
create_session session-b-created.json

session_a="$(python -c 'import json; print(json.load(open("session-a-created.json"))["data"]["id"])')"
session_b="$(python -c 'import json; print(json.load(open("session-b-created.json"))["data"]["id"])')"
dir_a="$(python -c 'import json; print(json.load(open("session-a-created.json"))["data"]["location"]["directory"])')"
dir_b="$(python -c 'import json; print(json.load(open("session-b-created.json"))["data"]["location"]["directory"])')"

test -n "$session_a"
test -n "$session_b"
test "$session_a" != "$session_b"
test "$dir_a" = "$fixture"
test "$dir_b" = "$fixture"

# Neither session starts with a model selection. This makes the isolation assertion
# unambiguous and avoids any provider lookup, auth, completion, or quota use.
python - <<'PY'
import json
for path in ("session-a-created.json", "session-b-created.json"):
    data = json.load(open(path))["data"]
    assert data.get("model") is None, (path, data.get("model"))
    assert data.get("cost") == 0, (path, data.get("cost"))
    assert data.get("tokens") == {
        "input": 0,
        "output": 0,
        "reasoning": 0,
        "cache": {"read": 0, "write": 0},
    }, (path, data.get("tokens"))
PY

command_payload='{"text":"/orchestrator-route","command":"orchestrator-route","arguments":""}'

timeout 30 opencode2 api post "/api/session/$session_a/command" \
  --header "x-opencode-directory: $fixture" \
  --data "$command_payload" > active-command.json
cat active-command.json

for attempt in $(seq 1 20); do
  if grep -q "\"session_id\": \"$session_a\"" "$daemon_log"; then
    break
  fi
  sleep 0.25
done
grep -q '"event": "route_request"' "$daemon_log"
grep -q '"mode": "ACTIVE"' "$daemon_log"
grep -q "\"session_id\": \"$session_a\"" "$daemon_log"
if grep -q "\"session_id\": \"$session_b\"" "$daemon_log"; then
  echo 'inactive session B unexpectedly reached the routing daemon'
  exit 1
fi

# Read both sessions from the same real OpenCode server after A's ACTIVE command.
timeout 30 opencode2 api get "/api/session/$session_a" \
  --header "x-opencode-directory: $fixture" > session-a-after.json
timeout 30 opencode2 api get "/api/session/$session_b" \
  --header "x-opencode-directory: $fixture" > session-b-after.json
cat session-a-after.json
cat session-b-after.json

python - <<'PY'
import json

a = json.load(open("session-a-after.json"))["data"]
b = json.load(open("session-b-after.json"))["data"]

model = a.get("model") or {}
assert model.get("providerID") == "spike-provider", model
assert model.get("id") == "target-model", model
assert model.get("variant") in (None, "default"), model
assert b.get("model") is None, b.get("model")

for label, session in (("A", a), ("B", b)):
    assert session.get("cost") == 0, (label, session.get("cost"))
    assert session.get("tokens") == {
        "input": 0,
        "output": 0,
        "reasoning": 0,
        "cache": {"read": 0, "write": 0},
    }, (label, session.get("tokens"))
PY

# Durable history should contain the model switch only for A. The exact endpoint is
# part of the pinned beta contract; if unavailable, session-state isolation above is
# already authoritative, so history is collected as best-effort evidence only.
opencode2 api get "/api/session/$session_a/history" \
  --header "x-opencode-directory: $fixture" > history-a.json 2>/dev/null || true
opencode2 api get "/api/session/$session_b/history" \
  --header "x-opencode-directory: $fixture" > history-b.json 2>/dev/null || true

if grep -q 'session.next.model.switched' history-b.json 2>/dev/null; then
  echo 'session B unexpectedly contains a model-switched durable event'
  exit 1
fi

route_count="$(grep -c '"event": "route_request"' "$daemon_log")"
test "$route_count" -eq 1

echo '--- fake daemon ACTIVE evidence ---'
cat "$daemon_log"
echo "session A: $session_a -> spike-provider/target-model"
echo "session B: $session_b -> unchanged"
echo "ACTIVE route count: $route_count"
echo 'provider completion/token usage: ZERO'
echo 'ACTIVE session isolation: PASS'
