import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("entry", [
    [str(ROOT / "tcapsule"), "--help"],
    ["-m", "timecapsulesmb.cli.main", "--help"],
    ["-c", "from timecapsulesmb.cli.main import main; raise SystemExit(main(['--help']))"],
])
def test_cli_entrypoints_reach_help(entry, tmp_path):
    result = subprocess.run(
        [sys.executable, *entry], cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert "TimeCapsuleSMB command line interface" in result.stdout
    assert list(tmp_path.iterdir()) == []


@pytest.mark.skipif(sys.platform != "win32", reason="Windows desktop integration")
def test_desktop_validates_installation_from_background_worker(tmp_path):
    import tkinter as tk

    from timecapsulesmb.windows_gui import Desktop, configure_tcl

    configure_tcl()
    window = tk.Tk()
    window.withdraw()
    desktop = Desktop(window, tmp_path)
    result = []
    desktop._run("validate-install", {}, result.append)

    def finish():
        if not desktop.busy:
            window.quit()
        else:
            window.after(30, finish)

    window.after(30, finish)
    window.after(10000, window.quit)
    try:
        window.mainloop()
        assert result and result[0]["checks"]
        assert all(check["ok"] for check in result[0]["checks"])
    finally:
        window.destroy()
