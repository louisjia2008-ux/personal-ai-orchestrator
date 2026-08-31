#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PACKAGE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_DIR="$(cd "${PACKAGE_DIR}/../.." && pwd)"
APP_NAME="Personal AI Orchestrator"
DIST_DIR="${PACKAGE_DIR}/dist"
APP_DIR="${DIST_DIR}/${APP_NAME}.app"
HELPERS_DIR="${APP_DIR}/Contents/Helpers"
WIDGET_APPEX="${APP_DIR}/Contents/PlugIns/PAOWidgetExtension.appex"
XCODE_PROJECT="${PACKAGE_DIR}/PAOMenuBar.xcodeproj"
XCODE_DERIVED="${PACKAGE_DIR}/.build/xcderived"
XCODE_APP="${XCODE_DERIVED}/Build/Products/Release/${APP_NAME}.app"
HOST_ENTITLEMENTS="${PACKAGE_DIR}/Config/PersonalAIOrchestrator.entitlements"
WIDGET_ENTITLEMENTS="${PACKAGE_DIR}/Config/PAOWidgetExtension.entitlements"
CODESIGN_IDENTITY="${PAO_CODESIGN_IDENTITY:--}"
PYTHON="${PAO_PACKAGING_PYTHON:-${REPO_DIR}/.venv/bin/python}"
DAEMON_DIST="${PACKAGE_DIR}/.build/pao-daemon-dist"
DAEMON_BUILD="${PACKAGE_DIR}/.build/pao-daemon-build"
DAEMON_SPEC="${PACKAGE_DIR}/.build/pao-daemon-spec"

xcodegen generate --spec "${PACKAGE_DIR}/project.yml"

xcodebuild \
  -project "${XCODE_PROJECT}" \
  -scheme "${APP_NAME}" \
  -configuration Release \
  -derivedDataPath "${XCODE_DERIVED}" \
  CODE_SIGN_STYLE=Manual \
  CODE_SIGN_IDENTITY="-" \
  DEVELOPMENT_TEAM="" \
  CODE_SIGN_ENTITLEMENTS="" \
  build

if [[ ! -x "${PYTHON}" ]]; then
  echo "packaging python not found: ${PYTHON}" >&2
  echo "Create a project-local environment and install .[macos-package]." >&2
  exit 2
fi

if ! "${PYTHON}" -m PyInstaller --version >/dev/null 2>&1; then
  echo "PyInstaller is not installed in ${PYTHON}." >&2
  echo "Install project-local packaging deps: ${PYTHON} -m pip install -e '.[macos-package]'." >&2
  exit 2
fi

"${PYTHON}" -m PyInstaller \
  --clean \
  --noconfirm \
  --onefile \
  --name pao-daemon \
  --distpath "${DAEMON_DIST}" \
  --workpath "${DAEMON_BUILD}" \
  --specpath "${DAEMON_SPEC}" \
  "${REPO_DIR}/scripts/pao_daemon_entry.py"

rm -rf "${APP_DIR}"
mkdir -p "${DIST_DIR}"
ditto "${XCODE_APP}" "${APP_DIR}"

mkdir -p "${HELPERS_DIR}"
cp "${DAEMON_DIST}/pao-daemon" "${HELPERS_DIR}/pao-daemon"
chmod 755 "${HELPERS_DIR}/pao-daemon"

codesign --force --sign "${CODESIGN_IDENTITY}" "${HELPERS_DIR}/pao-daemon"
codesign --force --sign "${CODESIGN_IDENTITY}" --entitlements "${WIDGET_ENTITLEMENTS}" "${WIDGET_APPEX}"
codesign --force --sign "${CODESIGN_IDENTITY}" --entitlements "${HOST_ENTITLEMENTS}" "${APP_DIR}"
codesign --verify --strict --verbose=2 "${HELPERS_DIR}/pao-daemon"
codesign --verify --strict --verbose=2 "${WIDGET_APPEX}"
codesign --verify --deep --strict --verbose=2 "${APP_DIR}"

echo "${APP_DIR}"
