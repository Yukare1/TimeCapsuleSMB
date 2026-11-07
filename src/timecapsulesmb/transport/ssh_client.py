"""Validate the local OpenSSH required by the password helper."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

from timecapsulesmb.core.process import run_process
from timecapsulesmb.transport.errors import SshClientConfigError, ssh_signal_error


def windows_ssh_path() -> str:
    """Git for Windows supplies the POSIX askpass and legacy SSH algorithms."""
    override = os.getenv("TCAPSULE_WINDOWS_SSH")
    if override:
        candidate = Path(override)
        if candidate.is_absolute() and candidate.is_file():
            return str(candidate)
        raise SshClientConfigError("TCAPSULE_WINDOWS_SSH must point to Git for Windows' ssh.exe.")
    git = shutil.which("git")
    roots = [Path(git).parent.parent] if git else []
    roots.extend(Path(os.environ[key]) / "Git" for key in ("ProgramFiles", "ProgramFiles(x86)") if key in os.environ)
    roots.append(Path.home() / "AppData" / "Local" / "Programs" / "Git")
    for root in roots:
        path = root / "usr" / "bin" / "ssh.exe"
        if path.is_file():
            return str(path)
    raise SshClientConfigError("Install Git for Windows, or set TCAPSULE_WINDOWS_SSH to its usr/bin/ssh.exe.")


def require_local_ssh() -> str:
    """Return the absolute executable whose version was checked."""
    path = windows_ssh_path() if sys.platform == "win32" else shutil.which("ssh")
    if path is None:
        raise SshClientConfigError("Local tool ssh is missing; install OpenSSH 8.4 or newer on your computer.")
    path = os.path.abspath(path)
    _validate_ssh(path)
    return path


@lru_cache(maxsize=16)
def _validate_ssh(path: str) -> None:
    # Deploy runs hundreds of commands. Cache successful checks by executable,
    # but let failures be retried after bootstrap installs or repairs ssh.
    try:
        result = run_process([path, "-V"], capture_output=True, text=True, errors="replace", timeout=5)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SshClientConfigError(f"Could not check local SSH client {path}: {exc}") from exc
    if result.returncode < 0:
        raise ssh_signal_error(-result.returncode)
    output = (result.stderr or "") + (result.stdout or "")
    if sys.platform == "win32" and "OpenSSH_for_Windows" in output:
        raise SshClientConfigError("Use the ssh.exe bundled with Git for Windows for password authentication.")
    version = re.search(r"OpenSSH_(\d+)\.(\d+)", output)
    if result.returncode or version is None:
        raise SshClientConfigError(f"Could not identify local SSH client {path}; OpenSSH 8.4 or newer is required.")
    if tuple(map(int, version.groups())) < (8, 4):
        raise SshClientConfigError(
            f"Local SSH client {path} is {version.group(0)}; OpenSSH 8.4 or newer is required. "
            "Upgrade OpenSSH on your computer and retry."
        )
