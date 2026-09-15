import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from personal_ai_orchestrator.control_client import ControlPlaneClient
from personal_ai_orchestrator.runtime_config import default_application_support_layout
from personal_ai_orchestrator.supervised_auto_step import SUPERVISED_AUTO_STEP_NAME


def test_product_daemon_health_exposes_supervised_auto_step() -> None:
    """The real product process must host the AUTO tick even though it is control-only.

    This is intentionally stronger than argv/parser coverage: it launches the
    packaged entrypoint contract, waits for the UDS control plane, and asks the
    daemon's own health projection which supervisor steps are actually registered.
    No project is registered, scheduling remains MANUAL, and no model call can run.
    """

    home = Path(tempfile.mkdtemp(prefix="pao-product-auto-step-", dir="/tmp"))
    layout = default_application_support_layout(home)
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "personal_ai_orchestrator.product_daemon",
            "--home",
            str(home),
            "--port",
            "0",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 15.0
        while not layout.socket_path.exists():
            if process.poll() is not None:
                out, err = process.communicate(timeout=5)
                raise AssertionError(f"product daemon exited early: {out}\n{err}")
            if time.monotonic() > deadline:
                raise AssertionError("product daemon never created the control socket")
            time.sleep(0.1)

        health = ControlPlaneClient(layout.socket_path).health()
        names = {step.name for step in health.supervisor_steps}
        assert SUPERVISED_AUTO_STEP_NAME in names
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
            process.communicate(timeout=15)
        if process.poll() is None:  # pragma: no cover - defensive cleanup
            process.kill()
            process.wait(timeout=5)

    assert process.returncode == 0
    assert not layout.socket_path.exists()
