from pathlib import Path


def test_packaged_helper_resolves_sources_from_the_exact_checkout() -> None:
    """An editable packaging venv must not silently freeze another checkout."""

    repository = Path(__file__).resolve().parents[1]
    script = repository / "macos" / "PAOMenuBar" / "scripts" / "build_app_bundle.sh"
    source = script.read_text(encoding="utf-8")

    assert '--paths "${REPO_DIR}/src"' in source
    assert '"${REPO_DIR}/scripts/pao_daemon_entry.py"' in source
