#!/usr/bin/env bash
# Stage C provider-native auth + catalog preflight.
#
# Verifies, using only OpenCode's supported read-only metadata surfaces:
#   1. the OpenCode executable is present and reports its version;
#   2. the target provider has a real credential in OpenCode's own auth store;
#   3. the target model exists in the real authenticated OpenCode catalog.
#
# It NEVER reads, prints, copies, or transmits credential contents. Provider
# authentication is verified by presence/type metadata only (provider display
# name in the auth.json credentials block), exactly the kind of non-secret
# evidence Stage C is allowed to record.
#
# Usage:
#   runtime_provider_auth_smoke.sh --provider <provider-id> --model <model-id> \
#       [--opencode <executable>]
#
# Exit codes: 0 = ready, 2 = auth absent, 3 = model absent, 4 = opencode missing.
set -euo pipefail

OPENCODE_BIN="opencode"
PROVIDER=""
MODEL=""
while [ $# -gt 0 ]; do
  case "$1" in
    --provider) PROVIDER="$2"; shift 2 ;;
    --model) MODEL="$2"; shift 2 ;;
    --opencode) OPENCODE_BIN="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 64 ;;
  esac
done

if [ -z "$PROVIDER" ] || [ -z "$MODEL" ]; then
  echo "usage: $0 --provider <provider-id> --model <model-id> [--opencode <bin>]" >&2
  exit 64
fi

if ! command -v "$OPENCODE_BIN" >/dev/null 2>&1; then
  echo "PREFLIGHT: opencode executable not found ($OPENCODE_BIN)" >&2
  exit 4
fi

VERSION="$("$OPENCODE_BIN" --version 2>/dev/null | tail -n1)"
echo "PREFLIGHT opencode_executable=$(command -v "$OPENCODE_BIN")"
echo "PREFLIGHT opencode_version=$VERSION"

# Map provider id -> display name from the real v2 catalog, then confirm that
# display name is present in the auth.json credentials block of `providers list`.
# All parsing is metadata-only; no credential values are ever inspected.
PROVIDERS_LIST="$("$OPENCODE_BIN" providers list 2>/dev/null || true)"
CATALOG_V2="$("$OPENCODE_BIN" debug v2 2>/dev/null || true)"
CACHE_DIR="$("$OPENCODE_BIN" debug paths 2>/dev/null | awk '$1=="cache"{print $2}')"
MODELS_JSON="${CACHE_DIR:-$HOME/.cache/opencode}/models.json"

# The probe is written to a temp file first; a here-document nested inside a
# command substitution is unreliable under the bash 3.2 that ships on macOS.
PROBE="$(mktemp)"
cat > "$PROBE" <<'PY'
import json
import os
import re
import sys

provider = os.environ["PROVIDER"]
providers_list = sys.argv[1]
models_json_path = sys.argv[2]
catalog_v2 = sys.argv[3]

# Resolve this provider id -> display name. Primary source is OpenCode's complete
# models.dev registry cache (models.json, non-secret). Fallback is `debug v2`,
# whose provider array only covers configured/multi-endpoint providers.
display_name = None
try:
    registry = json.load(open(models_json_path))
    container = registry.get("providers", registry)
    entry = container.get(provider) if isinstance(container, dict) else None
    if isinstance(entry, dict):
        display_name = entry.get("name")
except (OSError, ValueError, json.JSONDecodeError):
    pass

if not display_name:
    try:
        start = catalog_v2.index("{")
        catalog = json.loads(catalog_v2[start:])
        for entry in catalog.get("providers", []):
            if entry.get("id") == provider:
                display_name = entry.get("name")
                break
    except (ValueError, json.JSONDecodeError):
        pass

# Restrict to the auth.json "Credentials" block (exclude the "Environment" block,
# which merely enumerates registry-known env var names, not present credentials).
clean = re.sub(r"\x1b\[[0-9;]*m", "", providers_list)
cred_block = clean.split("Environment", 1)[0]

authed = bool(display_name) and display_name in cred_block
print(display_name or "UNKNOWN")
print("True" if authed else "False")
PY

AUTH_OUT="$(PROVIDER="$PROVIDER" python3 "$PROBE" "$PROVIDERS_LIST" "$MODELS_JSON" "$CATALOG_V2")"
rm -f "$PROBE"
DISPLAY_NAME="$(printf '%s\n' "$AUTH_OUT" | sed -n 1p)"
AUTHED="$(printf '%s\n' "$AUTH_OUT" | sed -n 2p)"

echo "PREFLIGHT provider_id=$PROVIDER"
echo "PREFLIGHT provider_display_name=$DISPLAY_NAME"
echo "PREFLIGHT auth_present=$AUTHED credential_type=api_or_oauth_metadata_only"

if [ "$AUTHED" != "True" ]; then
  echo "PREFLIGHT: provider '$PROVIDER' has no credential in the OpenCode auth store; refusing." >&2
  exit 2
fi

# Confirm the model is present in the real authenticated catalog.
if "$OPENCODE_BIN" models "$PROVIDER" 2>/dev/null | grep -qx "$PROVIDER/$MODEL"; then
  echo "PREFLIGHT model_in_catalog=YES model=$PROVIDER/$MODEL"
else
  echo "PREFLIGHT: model '$PROVIDER/$MODEL' not found in the authenticated catalog" >&2
  exit 3
fi

echo "PREFLIGHT: READY provider=$PROVIDER model=$MODEL"
