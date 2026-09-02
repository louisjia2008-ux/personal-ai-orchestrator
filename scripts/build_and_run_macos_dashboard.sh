#!/usr/bin/env bash
#
# Deterministic build-and-run for the macOS dashboard.
#
# Every copy of the app shares one bundle identifier and one version string, so
# LaunchServices cannot tell a freshly built app from a year-old one in
# /Applications. This script removes that ambiguity end to end: it builds from
# the current checkout, proves the produced bundle carries the current commit,
# stops only the exact process it identified, launches the exact bundle it just
# built, and then proves the process now running came from that bundle.
#
# It never uses killall or pkill: only PIDs whose executable path was verified
# to live inside a PAO bundle are ever signalled.
#
# Usage:
#   scripts/build_and_run_macos_dashboard.sh [--expect-branch <name>] [--no-launch]
#
set -euo pipefail

APP_NAME="Personal AI Orchestrator"
EXPECT_BRANCH=""
DO_LAUNCH=1
TERM_GRACE_SECONDS=10

while [[ $# -gt 0 ]]; do
  case "$1" in
    --expect-branch) EXPECT_BRANCH="${2:-}"; shift 2 ;;
    --no-launch)     DO_LAUNCH=0; shift ;;
    -h|--help)       sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

log() { printf '[build-and-run] %s\n' "$*" >&2; }
fail() { printf '[build-and-run] ERROR: %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- 1. git root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(git -C "${SCRIPT_DIR}" rev-parse --show-toplevel 2>/dev/null)" \
  || fail "not inside a git checkout: ${SCRIPT_DIR}"

# ------------------------------------------------------- 2. capture exact HEAD
SOURCE_HEAD="$(git -C "${REPO_DIR}" rev-parse HEAD)"
[[ "${SOURCE_HEAD}" =~ ^[0-9a-f]{40}$ ]] || fail "unresolvable HEAD: ${SOURCE_HEAD}"
SOURCE_SHORT="${SOURCE_HEAD:0:7}"
BRANCH="$(git -C "${REPO_DIR}" rev-parse --abbrev-ref HEAD)"

log "repo    ${REPO_DIR}"
log "branch  ${BRANCH}"
log "HEAD    ${SOURCE_HEAD}"

# ------------------------------------------------- 3. verify expected branch
if [[ -n "${EXPECT_BRANCH}" && "${BRANCH}" != "${EXPECT_BRANCH}" ]]; then
  fail "expected branch ${EXPECT_BRANCH}, on ${BRANCH}"
fi

if [[ -n "$(git -C "${REPO_DIR}" status --porcelain --untracked-files=no)" ]]; then
  log "WARNING: tracked files are modified; the build will not match ${SOURCE_SHORT} exactly"
fi

# ------------ helpers: identify PAO processes by verified executable path -----
# A candidate is only ever signalled when its executable path resolves inside a
# bundle named "<APP_NAME>.app". Nothing is matched by process name alone.
pao_gui_pids() {
  local pid comm
  while IFS= read -r line; do
    pid="${line%% *}"
    comm="${line#* }"
    [[ "${comm}" == *"/${APP_NAME}.app/Contents/MacOS/${APP_NAME}" ]] && printf '%s\n' "${pid}"
  done < <(ps -Ao pid=,comm= | sed 's/^ *//')
}

executable_for_pid() { ps -o comm= -p "$1" 2>/dev/null || true; }

bundle_for_executable() {
  local exe="$1"
  printf '%s\n' "${exe%/Contents/MacOS/*}"
}

embedded_commit() {
  /usr/libexec/PlistBuddy -c 'Print PAOBuildCommit' "$1/Contents/Info.plist" 2>/dev/null || true
}

# ------------------------------------------- 4. record the currently running app
OLD_PIDS=()
while IFS= read -r pid; do [[ -n "${pid}" ]] && OLD_PIDS+=("${pid}"); done < <(pao_gui_pids)

if [[ ${#OLD_PIDS[@]} -eq 0 ]]; then
  log "no ${APP_NAME} GUI process is running"
else
  for pid in "${OLD_PIDS[@]}"; do
    exe="$(executable_for_pid "${pid}")"
    bundle="$(bundle_for_executable "${exe}")"
    log "running PID ${pid}"
    log "  executable ${exe}"
    old_commit="$(embedded_commit "${bundle}")"
    log "  build      ${old_commit:-<none embedded>}"
  done
fi

# --------------------------------------------------- 5. build from this checkout
# Output lives under a path keyed to the commit and configuration, so two builds
# of different sources can never collide in one ambiguous product directory.
export PAO_XCODE_DERIVED="${REPO_DIR}/macos/PAOMenuBar/.build/DerivedData/Pao-${SOURCE_SHORT}-Release"
log "building Release from ${SOURCE_SHORT} ..."
APP_PATH="$(bash "${REPO_DIR}/macos/PAOMenuBar/scripts/build_app_bundle.sh" | tail -1)"
[[ -d "${APP_PATH}" ]] || fail "build did not produce an app bundle (got: ${APP_PATH})"
log "built ${APP_PATH}"

# ---------------------------------------------- 6/7. inspect embedded identity
BUILT_COMMIT="$(embedded_commit "${APP_PATH}")"
[[ -n "${BUILT_COMMIT}" ]] || fail "built bundle carries no PAOBuildCommit"
if [[ "${BUILT_COMMIT}" != "${SOURCE_HEAD}" ]]; then
  fail "built bundle reports ${BUILT_COMMIT}, source HEAD is ${SOURCE_HEAD}"
fi
log "bundle build identity verified: ${BUILT_COMMIT:0:7}"

if [[ ${DO_LAUNCH} -eq 0 ]]; then
  log "--no-launch: stopping after build verification"
  printf '%s\n' "${APP_PATH}"
  exit 0
fi

# ------------------------------ 8/9. stop ONLY the exact processes we identified
for pid in "${OLD_PIDS[@]:-}"; do
  [[ -n "${pid}" ]] || continue
  log "terminating PID ${pid} (SIGTERM)"
  kill -TERM "${pid}" 2>/dev/null || true
done

for pid in "${OLD_PIDS[@]:-}"; do
  [[ -n "${pid}" ]] || continue
  waited=0
  while kill -0 "${pid}" 2>/dev/null; do
    if (( waited >= TERM_GRACE_SECONDS )); then
      # Escalation is scoped to this one verified PID, never a name pattern.
      log "PID ${pid} did not exit in ${TERM_GRACE_SECONDS}s; sending SIGKILL to that PID only"
      kill -KILL "${pid}" 2>/dev/null || true
      sleep 1
      break
    fi
    sleep 1
    waited=$(( waited + 1 ))
  done
  if kill -0 "${pid}" 2>/dev/null; then
    fail "PID ${pid} is still running; refusing to launch a second instance"
  fi
  log "PID ${pid} exited"
done

# --------------------------------------------- 10. launch the exact new bundle
# `open -n <path>` targets this bundle by path and forces a new instance, so a
# same-identifier app elsewhere on disk cannot be reactivated in its place.
log "launching ${APP_PATH}"
open -n "${APP_PATH}"

# ------------------------------------------ 11/12. prove the new process origin
EXPECTED_EXEC="${APP_PATH}/Contents/MacOS/${APP_NAME}"
NEW_PID=""
for _ in $(seq 1 30); do
  while IFS= read -r pid; do
    [[ -n "${pid}" ]] || continue
    if [[ "$(executable_for_pid "${pid}")" == "${EXPECTED_EXEC}" ]]; then
      NEW_PID="${pid}"
      break
    fi
  done < <(pao_gui_pids)
  [[ -n "${NEW_PID}" ]] && break
  sleep 1
done

[[ -n "${NEW_PID}" ]] || fail "no process started from ${EXPECTED_EXEC}"

NEW_EXEC="$(executable_for_pid "${NEW_PID}")"
[[ "${NEW_EXEC}" == "${EXPECTED_EXEC}" ]] \
  || fail "running executable ${NEW_EXEC} is not the bundle we built"

# -------------------------------------------- 13. prove running SHA == HEAD
RUNNING_COMMIT="$(embedded_commit "$(bundle_for_executable "${NEW_EXEC}")")"
[[ "${RUNNING_COMMIT}" == "${SOURCE_HEAD}" ]] \
  || fail "running app reports ${RUNNING_COMMIT}, source HEAD is ${SOURCE_HEAD}"

log "----------------------------------------"
log "NEW_PID           ${NEW_PID}"
log "NEW_EXECUTABLE    ${NEW_EXEC}"
log "RUNNING_BUILD_SHA ${RUNNING_COMMIT}"
log "SOURCE_HEAD       ${SOURCE_HEAD}"
log "verified: the running app was built from this checkout"
printf '%s\n' "${APP_PATH}"
