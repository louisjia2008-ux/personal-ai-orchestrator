#!/usr/bin/env bash
#
# Captures one PNG per dashboard destination from the real built app, at an
# exact window size.
#
# XCUITest cannot resize a macOS window and the accessibility route is denied to
# the test runner, so the size is set the way AppKit itself sets it: through the
# window's autosaved frame, written before the app launches. The persisted split
# positions are cleared at the same time, so the capture shows the layout the
# code specifies rather than a divider the owner once dragged.
#
# Usage: scripts/capture_dashboard_screenshots.sh <width> <height> <outdir>
set -euo pipefail

WIDTH="${1:?width}"
HEIGHT="${2:?height}"
OUTDIR="${3:?output directory}"
# The bundle to drive. Defaults to the Release app the build script produces,
# which is the artifact the owner actually opens.
APP_PATH="${4:-}"

PACKAGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOMAIN="com.personal-ai-orchestrator.dashboard"
LABEL="${WIDTH}x${HEIGHT}"

# Screen geometry the autosaved frame string is expressed against. AppKit only
# uses it to decide whether the remembered frame still fits the current display,
# so it is read from the attached display rather than assumed.
# AppKit records the screen's *visible* rect alongside the frame and discards a
# saved frame whose screen no longer matches, so this has to be the rect AppKit
# itself uses — not the display's pixel resolution. The app writes it every time
# it quits, so the previous value is the authority; the fallback only matters on
# a machine where the app has never run.
SCREEN_RECT="$(
  defaults read "${DOMAIN}" "NSWindow Frame dashboard-AppWindow-1" 2>/dev/null \
    | awk '{ if (NF >= 8) printf "%s %s %s %s", $5, $6, $7, $8 }'
)"
[[ "${SCREEN_RECT}" =~ ^[0-9]+\ [0-9]+\ [0-9]+\ [0-9]+$ ]] || SCREEN_RECT="0 0 2560 1410"

mkdir -p "${OUTDIR}"

# Position the window away from the menu bar, at exactly the requested size.
# Low on the screen on purpose: macOS floats its permission prompts near the
# top, and a window placed under them is captured without an unrelated system
# alert sitting over its toolbar.
defaults write "${DOMAIN}" "NSWindow Frame dashboard-AppWindow-1" \
  "80 110 ${WIDTH} ${HEIGHT} ${SCREEN_RECT}"
# Forget any dragged divider: the capture must show the shipped split.
defaults delete "${DOMAIN}" "NSSplitView Subview Frames dashboard-AppWindow-1, SidebarNavigationSplitView" 2>/dev/null || true
defaults delete "${DOMAIN}" "NSSplitView Subview Frames dashboard-AppWindow-1" 2>/dev/null || true

# cfprefsd keeps the domain in memory, and the app rewrites its frame when it
# quits. Verify the value we just wrote is the value that will be read back,
# rather than launching against a stale frame and capturing the wrong size.
EXPECTED_FRAME="80 110 ${WIDTH} ${HEIGHT} ${SCREEN_RECT}"
for _ in 1 2 3; do
  ACTUAL_FRAME="$(defaults read "${DOMAIN}" "NSWindow Frame dashboard-AppWindow-1" 2>/dev/null || true)"
  [[ "${ACTUAL_FRAME}" == "${EXPECTED_FRAME}" ]] && break
  defaults write "${DOMAIN}" "NSWindow Frame dashboard-AppWindow-1" "${EXPECTED_FRAME}"
  sleep 1
done
[[ "${ACTUAL_FRAME}" == "${EXPECTED_FRAME}" ]] \
  || echo "WARNING: window frame default is '${ACTUAL_FRAME}', wanted '${EXPECTED_FRAME}'" >&2

cd "${PACKAGE_DIR}"
xcodegen generate --spec project.yml >/dev/null

LOG="$(mktemp -t pao-capture)"
set +e
TEST_RUNNER_PAO_SCREENSHOT_DIR="${OUTDIR}" \
TEST_RUNNER_PAO_SCREENSHOT_LABEL="${LABEL}" \
 TEST_RUNNER_PAO_SCREENSHOT_SIZE="${WIDTH}x${HEIGHT}" \
TEST_RUNNER_PAO_APP_PATH="${APP_PATH}" \
TEST_RUNNER_PAO_SCREENSHOT_FULLSCREEN="${PAO_SCREENSHOT_FULLSCREEN:-}" \
TEST_RUNNER_PAO_SCREENSHOT_FRAMES="${PAO_SCREENSHOT_FRAMES:-}" \
xcodebuild test \
  -project PAOMenuBar.xcodeproj \
  -scheme "Personal AI Orchestrator" \
  -configuration Debug \
  -derivedDataPath .build/uitest-derived \
  -only-testing:PAODashboardUITests/PAOScreenshotCapture \
  -destination 'platform=macOS,arch=arm64' \
  CODE_SIGN_STYLE=Manual CODE_SIGN_IDENTITY="-" \
  DEVELOPMENT_TEAM="" CODE_SIGN_ENTITLEMENTS="" 2>&1 | tee "${LOG}"
STATUS="${PIPESTATUS[0]}"
set -e

# The sandboxed runner writes into its own temporary directory; collect the
# exact files it reported writing.
COUNT=0
while IFS= read -r written; do
  cp "${written}" "${OUTDIR}/"
  COUNT=$(( COUNT + 1 ))
done < <(grep -o "PAO_SCREENSHOT_WROTE .*\.png" "${LOG}" | sed 's/^PAO_SCREENSHOT_WROTE //' | sort -u)

grep -o "PAO_WINDOW_FRAME .*" "${LOG}" | head -1
echo "captured ${COUNT} screenshots into ${OUTDIR}"
[[ "${COUNT}" -gt 0 ]] || exit "${STATUS}"
