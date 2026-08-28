#!/usr/bin/env bash
set -euo pipefail

fixture="$(mktemp -d)"
daemon_log="$fixture/fake-daemon.log"
daemon_pid=""

mkdir -p "$fixture/xdg" "$fixture/plugins"
git -C "$fixture" init -q
git -C "$fixture" config user.email "spike@example.invalid"
git -C "$fixture" config user.name "OpenCode Spike"
printf '# disposable OpenCode Shadow fixture\n' > "$fixture/README.md"
git -C "$fixture" add README.md
git -C "$fixture" commit -q -m "fixture: initialize"

cp "$GITHUB_WORKSPACE/integrations/opencode/plugin.ts" \
  "$fixture/plugins/orchestrator.ts"
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
        "mode": "SHADOW"
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
  --model minimax/m3 > "$daemon_log" 2>&1 &
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

# Wait until this project's configured command is actually visible.
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

# beta-18387's V2 /api surface scopes new sessions with location.directory in
# the JSON envelope. Header/query-only attempts create a session at the shared
# service cwd (/home/runner), so the body binding is a hard isolation contract.
session_payload="$(python -c \
  'import json,sys; print(json.dumps({"location":{"directory":sys.argv[1]}}))' \
  "$fixture")"
timeout 30 opencode2 api post /api/session \
  --header "x-opencode-directory: $fixture" \
  --data "$session_payload" > session.json
cat session.json
session_id="$(python -c 'import json; print(json.load(open("session.json"))["data"]["id"])')"
session_dir="$(python -c 'import json; print(json.load(open("session.json"))["data"]["location"]["directory"])')"
test -n "$session_id"
if [ "$session_dir" != "$fixture" ]; then
  echo "session escaped disposable workspace: $session_dir != $fixture"
  exit 1
fi

# beta-18387's /api compatibility surface requires a textual command envelope
# in addition to command + arguments.
command_payload='{"text":"/orchestrator-route","command":"orchestrator-route","arguments":""}'

# SHADOW must contact the daemon but must not switch the session model.
timeout 30 opencode2 api post "/api/session/$session_id/command" \
  --header "x-opencode-directory: $fixture" \
  --data "$command_payload" > shadow-command.json
cat shadow-command.json

for attempt in $(seq 1 20); do
  if grep -q "\"session_id\": \"$session_id\"" "$daemon_log"; then
    break
  fi
  sleep 0.25
done
grep -q '"event": "route_request"' "$daemon_log"
grep -q '"mode": "SHADOW"' "$daemon_log"
grep -q "\"session_id\": \"$session_id\"" "$daemon_log"

route_count_before="$(grep -c '"'"'event"'"': "'"'route_request"'"'' "$daemon_log")"
test "$route_count_before" -eq 1

# Stop only the exact fake-daemon PID created by this job. The same command must
# remain non-fatal, proving daemon unavailability is a safe bypass.
kill "$daemon_pid"
wait "$daemon_pid" || true
daemon_pid=""

timeout 30 opencode2 api post "/api/session/$session_id/command" \
  --header "x-opencode-directory: $fixture" \
  --data "$command_payload" > bypass-command.json
cat bypass-command.json

route_count_after="$(grep -c '"'"'event"'"': "'"'route_request"'"'' "$daemon_log")"
test "$route_count_after" -eq "$route_count_before"

echo '--- fake daemon evidence ---'
cat "$daemon_log"
echo "session workspace: $session_dir"
echo "SHADOW route count: $route_count_before"
echo 'daemon-failure safe bypass: PASS'
