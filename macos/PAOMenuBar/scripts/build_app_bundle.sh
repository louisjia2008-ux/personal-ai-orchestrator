#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PACKAGE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
CONFIGURATION="${CONFIGURATION:-release}"
APP_NAME="Personal AI Orchestrator"
APP_DIR="${PACKAGE_DIR}/dist/${APP_NAME}.app"
CONTENTS_DIR="${APP_DIR}/Contents"
MACOS_DIR="${CONTENTS_DIR}/MacOS"
RESOURCES_DIR="${CONTENTS_DIR}/Resources"
BUILD_DIR="${PACKAGE_DIR}/.build/arm64-apple-macosx/${CONFIGURATION}"

swift build --package-path "${PACKAGE_DIR}" -c "${CONFIGURATION}"

rm -rf "${APP_DIR}"
mkdir -p "${MACOS_DIR}" "${RESOURCES_DIR}"

cp "${BUILD_DIR}/PAOMenuBar" "${MACOS_DIR}/Personal AI Orchestrator"
chmod 755 "${MACOS_DIR}/Personal AI Orchestrator"

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
