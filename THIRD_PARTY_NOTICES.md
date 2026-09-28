# Third-party notices

Personal AI Orchestrator is MIT-licensed. Its declared direct runtime, test,
build, and adapter dependencies retain their own licenses:

| Dependency | Use | License |
|---|---|---|
| Pydantic | Python runtime validation | MIT |
| TypeScript | OpenCode adapter build/typecheck | Apache-2.0 |
| `@opencode-ai/plugin` | OpenCode adapter API | MIT |
| pytest | Python tests | MIT |
| pytest-asyncio | Async Python tests | Apache-2.0 |
| pytest-rerunfailures | Explicitly marked flaky test support | MPL-2.0 |
| Ruff | Python lint/format | MIT |
| PyInstaller | Optional macOS helper packaging | GPL-2.0-or-later with the PyInstaller bootloader exception |
| Hatchling | Python package build backend | MIT |

This summary is not a substitute for the license files shipped by each
dependency. Release builders must preserve all notices and license texts
required by the exact resolved dependency versions and must review transitive
dependencies before distribution.
