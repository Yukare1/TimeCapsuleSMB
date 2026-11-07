"""Windows SMB checks using a temporary outbound credential context."""
from __future__ import annotations

from contextlib import contextmanager
import ctypes as c
from ctypes import wintypes as w
import ipaddress
from pathlib import Path
import time

from timecapsulesmb.checks.models import CheckResult, DOMAIN_COMPUTER
from timecapsulesmb.checks.smb import _new_listing_attempt, _smbclient_attempts_failure_summary, _smb_file_steps_on_mount


def unc_root(host: str) -> str:
    try:
        ipv6 = ipaddress.IPv6Address(host)
    except ValueError:
        pass
    else:
        host = str(ipv6).replace(":", "-").replace("%", "s") + ".ipv6-literal.net"
    return "\\\\" + host


@contextmanager
def _outbound_credentials(username, password):
    """Use a separate network logon without disturbing Explorer's SMB sessions."""
    security = c.WinDLL("advapi32", use_last_error=True)
    kernel = c.WinDLL("kernel32", use_last_error=True)
    security.LogonUserW.argtypes = (w.LPCWSTR, w.LPCWSTR, w.LPCWSTR, w.DWORD, w.DWORD, c.POINTER(w.HANDLE))
    security.ImpersonateLoggedOnUser.argtypes = (w.HANDLE,)
    security.RevertToSelf.argtypes = ()
    kernel.CloseHandle.argtypes = (w.HANDLE,)
    token = w.HANDLE()
    domain, separator, name = username.partition("\\")
    if not separator:
        name, domain = username, "."
    # LOGON32_LOGON_NEW_CREDENTIALS / LOGON32_PROVIDER_WINNT50.
    if not security.LogonUserW(name, domain, password, 9, 3, c.byref(token)):
        raise c.WinError(c.get_last_error())
    try:
        if not security.ImpersonateLoggedOnUser(token):
            raise c.WinError(c.get_last_error())
        try:
            yield
        finally:
            if not security.RevertToSelf():
                raise c.WinError(c.get_last_error())
    finally:
        kernel.CloseHandle(token)


class _ShareInfo(c.Structure):
    _fields_ = [("name", w.LPWSTR), ("kind", w.DWORD), ("remark", w.LPWSTR)]


def _disk_shares(host):
    netapi = c.WinDLL("netapi32")
    netapi.NetShareEnum.argtypes = (
        w.LPCWSTR, w.DWORD, c.POINTER(c.c_void_p), w.DWORD,
        c.POINTER(w.DWORD), c.POINTER(w.DWORD), c.POINTER(w.DWORD),
    )
    netapi.NetShareEnum.restype = w.DWORD
    netapi.NetApiBufferFree.argtypes = (c.c_void_p,)
    buffer = c.c_void_p()
    count, total = w.DWORD(), w.DWORD()
    try:
        status = netapi.NetShareEnum(host, 1, c.byref(buffer), 0xffffffff, c.byref(count), c.byref(total), None)
        if status:
            raise c.WinError(status)
        entries = c.cast(buffer, c.POINTER(_ShareInfo))
        return [entries[index].name for index in range(count.value) if entries[index].kind & 0xffff == 0]
    finally:
        if buffer:
            netapi.NetApiBufferFree(buffer)


def check_listing(username, password, targets, port, timeout):
    if port not in (None, 445):
        return CheckResult("FAIL", "Windows SMB share enumeration requires TCP 445; the SSH tunnel uses another port.",
                           domain=DOMAIN_COMPUTER)
    attempts = []
    for target in targets:
        start = time.monotonic()
        try:
            with _outbound_credentials(username, password):
                shares = _disk_shares(unc_root(target.ip_address or target.server))
        except OSError as exc:
            attempt = _new_listing_attempt(target, timeout, start)
            attempt.update(outcome="error", failure=str(exc), errno=getattr(exc, "winerror", None))
            attempts.append(attempt)
            continue
        attempt = _new_listing_attempt(target, timeout, start)
        attempt.update(outcome="pass", disk_shares=shares)
        attempts.append(attempt)
        return CheckResult("PASS", f"authenticated SMB listing works for {username}@{target.display}", {
            "server": target.server, "ip_address": target.ip_address, "disk_shares": shares, "attempts": attempts,
        })
    return CheckResult("FAIL", f"authenticated SMB listing failed: {_smbclient_attempts_failure_summary(attempts)}",
                       {"attempts": attempts})


def check_file_ops(username, password, host, share, port):
    if port not in (None, 445):
        return [CheckResult("WARN", "Windows SMB file checks require TCP 445.", domain=DOMAIN_COMPUTER)]
    if not share or share in {".", ".."} or any(char in share for char in "\\/:"):
        return [CheckResult("FAIL", "Invalid SMB share name.")]
    try:
        with _outbound_credentials(username, password):
            return _smb_file_steps_on_mount(Path(unc_root(host)) / share, f"{username}@{host}/{share}")
    except OSError as exc:
        return [CheckResult("FAIL", f"SMB access failed: {exc}", {"errno": getattr(exc, "winerror", None)})]
