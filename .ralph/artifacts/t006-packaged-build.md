# T006 packaged build identity

- Source HEAD: `b1d6a660be074ddff085f485835f8ec6e419e4d8`
- Build command: `PAO_PACKAGING_PYTHON=/private/tmp/pao-venv-short/bin/python bash macos/PAOMenuBar/scripts/build_app_bundle.sh`
- Build result: PASS, Release bundle completed at `2026-09-14T01:16:24Z`
- App: `macos/PAOMenuBar/dist/Personal AI Orchestrator.app`
- Embedded `PAOBuildCommit`: `b1d6a660be074ddff085f485835f8ec6e419e4d8`
- Embedded version/build: `1.0` / `1`
- Helper: `Contents/Helpers/pao-daemon`
- Helper SHA-256: `ad00de62d72b1103109a2521f1cdbc920be19b89bf9dd3714ac697f814e0f887`
- Helper architecture: Mach-O 64-bit arm64
- Host executable architecture: universal x86_64/arm64
- Signing: helper and bundle are ad-hoc signed; strict helper verification and deep strict bundle verification PASS.

The first in-sandbox invocation failed before compilation because Xcode and SwiftPM could not write the normal user cache directories. Re-running the identical repository build command with normal cache access passed; this is classified as an environment permission boundary, not a product failure.

This task proves build identity and signature integrity only. Runtime parent/child SIGTERM forwarding, socket cleanup, database release, and same-state restart are T007 acceptance gates.
