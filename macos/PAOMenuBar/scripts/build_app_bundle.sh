#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PACKAGE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_DIR="$(cd "${PACKAGE_DIR}/../.." && pwd)"
CONFIGURATION="${CONFIGURATION:-release}"
APP_NAME="Personal AI Orchestrator"
APP_DIR="${PACKAGE_DIR}/dist/${APP_NAME}.app"
CONTENTS_DIR="${APP_DIR}/Contents"
MACOS_DIR="${CONTENTS_DIR}/MacOS"
HELPERS_DIR="${CONTENTS_DIR}/Helpers"
RESOURCES_DIR="${CONTENTS_DIR}/Resources"
BUILD_DIR="${PACKAGE_DIR}/.build/arm64-apple-macosx/${CONFIGURATION}"
PYTHON="${PAO_PACKAGING_PYTHON:-${REPO_DIR}/.venv/bin/python}"
DAEMON_DIST="${PACKAGE_DIR}/.build/pao-daemon-dist"
DAEMON_BUILD="${PACKAGE_DIR}/.build/pao-daemon-build"
DAEMON_SPEC="${PACKAGE_DIR}/.build/pao-daemon-spec"

swift build --package-path "${PACKAGE_DIR}" -c "${CONFIGURATION}"

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
mkdir -p "${MACOS_DIR}" "${HELPERS_DIR}" "${RESOURCES_DIR}"

cp "${BUILD_DIR}/PAOMenuBar" "${MACOS_DIR}/Personal AI Orchestrator"
chmod 755 "${MACOS_DIR}/Personal AI Orchestrator"
cp "${DAEMON_DIST}/pao-daemon" "${HELPERS_DIR}/pao-daemon"
chmod 755 "${HELPERS_DIR}/pao-daemon"

if [[ -d "${BUILD_DIR}/PAOMenuBar_PAOControlKit.bundle" ]]; then
  cp -R "${BUILD_DIR}/PAOMenuBar_PAOControlKit.bundle" "${APP_DIR}/"
  cp -R "${BUILD_DIR}/PAOMenuBar_PAOControlKit.bundle" "${RESOURCES_DIR}/"
fi

cat > "${CONTENTS_DIR}/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key>
  <string>en</string>
  <key>CFBundleDisplayName</key>
  <string>Personal AI Orchestrator</string>
  <key>CFBundleExecutable</key>
  <string>Personal AI Orchestrator</string>
  <key>CFBundleIdentifier</key>
  <string>com.personal-ai-orchestrator.dashboard</string>
  <key>CFBundleInfoDictionaryVersion</key>
  <string>6.0</string>
  <key>CFBundleName</key>
  <string>Personal AI Orchestrator</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>0.0.1</string>
  <key>CFBundleVersion</key>
  <string>1</string>
  <key>LSMinimumSystemVersion</key>
  <string>13.0</string>
  <key>NSHumanReadableCopyright</key>
  <string>Copyright 2026</string>
</dict>
</plist>
PLIST

echo "${APP_DIR}"
