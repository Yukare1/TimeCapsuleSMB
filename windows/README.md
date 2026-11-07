# TimeCapsuleSMB for Windows

The Windows desktop port uses the same device backend and NetBSD Samba payloads as the original TimeCapsuleSMB. The desktop window has a device sidebar and Overview, Install / Update, Checkup, Maintenance and Settings tabs.

## Open the desktop app

1. Install [Git for Windows](https://gitforwindows.org/) with its bundled OpenSSH. The app uses this SSH client for password authentication and compatibility with AirPort firmware. For a custom or portable Git installation, set `TCAPSULE_WINDOWS_SSH` to the absolute path to its `usr/bin/ssh.exe`.
2. Extract the entire Windows ZIP and open `TimeCapsuleSMB.exe` inside the extracted folder. Keep `_internal` and `bin` beside the executable.
3. Connect the PC and the Time Capsule to the same local network. Choose **Discover**, or choose **Add Device** to enter the IP address manually. If the PC also has Wi-Fi or a VPN, select the wired adapter from the sidebar's adapter list before choosing **Discover**. Enter the AirPort device password.
4. In **Install / Update**, create and review the plan before choosing **Install / Update**. The device may reboot during installation. The app displays the backend's confirmation before proceeding.
5. Run **Checkup** and use **Open in Explorer** to browse the SMB server. If Explorer prompts for credentials, use the device password with an SMB username, for example `timecapsule`.

The desktop app saves device profiles under `%LOCALAPPDATA%\TimeCapsuleSMB\Desktop`. Device passwords are held in memory for the current app session. Diagnostics sharing is off on first launch and can be enabled in Settings.

Checkup uses the Windows SMB client directly over TCP 445. SMB checks over the backend's alternate-port SSH tunnel are unavailable on Windows. The Explorer button uses Windows' normal credential prompts. Firmware flashing and disk wiping are outside the scope of the Windows desktop interface.

## Run or build from source

On 64-bit Windows, install Git for Windows and 64-bit Python 3.13 with Tcl/Tk. In a clone of the Windows branch:

```powershell
py -3.13 .\tcapsule bootstrap
.\.venv\Scripts\timecapsulesmb.exe
```

To build the portable ZIP from a committed source revision:

```powershell
.\.venv\Scripts\python.exe -m pip install "pyinstaller>=6,<7"
.\windows\build.ps1 -Python .\.venv\Scripts\python.exe
```

This produces `dist\TimeCapsuleSMB-Windows-x64.zip`. The distribution includes the GPL-3.0 license and the corresponding source revision.
