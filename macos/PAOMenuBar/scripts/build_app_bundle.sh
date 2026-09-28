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
XCODE_DERIVED="${PAO_XCODE_DERIVED:-${PACKAGE_DIR}/.build/xcderived}"
XCODE_APP="${XCODE_DERIVED}/Build/Products/Release/${APP_NAME}.app"
HOST_ENTITLEMENTS="${PACKAGE_DIR}/Config/PersonalAIOrchestrator.entitlements"
WIDGET_ENTITLEMENTS="${PACKAGE_DIR}/Config/PAOWidgetExtension.entitlements"
CODESIGN_IDENTITY="${PAO_CODESIGN_IDENTITY:--}"
PYTHON="${PAO_PACKAGING_PYTHON:-${REPO_DIR}/.venv/bin/python}"
DAEMON_DIST="${PACKAGE_DIR}/.build/pao-daemon-dist"
DAEMON_BUILD="${PACKAGE_DIR}/.build/pao-daemon-build"
DAEMON_SPEC="${PACKAGE_DIR}/.build/pao-daemon-spec"
DAEMON_STAMP_DIR="${PACKAGE_DIR}/.build/pao-daemon-stamp"

# Build identity is resolved from Git here and injected into both halves of the
# product. It is never hard-coded: a checkout whose commit cannot be resolved
# fails the build rather than shipping a stale or invented SHA.
if ! BUILD_COMMIT="$(git -C "${REPO_DIR}" rev-parse HEAD 2>/dev/null)"; then
  echo "cannot resolve git commit for ${REPO_DIR}; refusing to build an unidentifiable app" >&2
  exit 2
fi
if [[ ! "${BUILD_COMMIT}" =~ ^[0-9a-f]{40}$ ]]; then
  echo "unexpected git commit value: ${BUILD_COMMIT}" >&2
  exit 2
fi
BUILD_SHORT="${BUILD_COMMIT:0:7}"
BUILD_TIMESTAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
BUILD_CONFIGURATION="Release"

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
  PAO_BUILD_COMMIT="${BUILD_COMMIT}" \
  PAO_BUILD_TIMESTAMP="${BUILD_TIMESTAMP}" \
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

# The frozen daemon has no checkout to consult at runtime, so the same commit is
# carried in as a data file and read back by personal_ai_orchestrator.build_identity.
rm -rf "${DAEMON_STAMP_DIR}"
mkdir -p "${DAEMON_STAMP_DIR}"
cat > "${DAEMON_STAMP_DIR}/_build_stamp.json" <<STAMP
{
  "commit_sha": "${BUILD_COMMIT}",
  "configuration": "${BUILD_CONFIGURATION}",
  "built_at": "${BUILD_TIMESTAMP}"
}
STAMP

# Force the checkout source ahead of any editable install carried by the
# packaging interpreter.  ``--paths`` alone is appended after interpreter
# paths and can otherwise freeze a different checkout of the same package.
PYTHONPATH="${REPO_DIR}/src" "${PYTHON}" -m PyInstaller \
  --clean \
  --noconfirm \
  --onefile \
  --name pao-daemon \
  --paths "${REPO_DIR}/src" \
  --distpath "${DAEMON_DIST}" \
  --workpath "${DAEMON_BUILD}" \
  --specpath "${DAEMON_SPEC}" \
  --add-data "${DAEMON_STAMP_DIR}/_build_stamp.json:personal_ai_orchestrator" \
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

# Fail loudly if the shipped bundle does not carry the commit we just built.
EMBEDDED_COMMIT="$(/usr/libexec/PlistBuddy -c 'Print PAOBuildCommit' "${APP_DIR}/Contents/Info.plist" 2>/dev/null || true)"
if [[ "${EMBEDDED_COMMIT}" != "${BUILD_COMMIT}" ]]; then
  echo "bundle build identity mismatch: embedded='${EMBEDDED_COMMIT}' expected='${BUILD_COMMIT}'" >&2
  exit 3
fi

echo "built ${BUILD_SHORT} (${BUILD_CONFIGURATION}) at ${BUILD_TIMESTAMP}" >&2
echo "${APP_DIR}"
