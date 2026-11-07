"""Windows desktop frontend for the same app operations used by the Mac app."""
from __future__ import annotations

import ipaddress
import json
import os
from pathlib import Path
from queue import Empty, Queue
import sys
from threading import Thread
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk
from uuid import uuid4

from ifaddr import get_adapters

from timecapsulesmb.app.events import EventSink
from timecapsulesmb.app.service import run_api_request
from timecapsulesmb.checks.smb_windows import unc_root
from timecapsulesmb.core.net import endpoint_host
from timecapsulesmb.core.paths import default_user_data_dir
from timecapsulesmb.core.release import CLI_VERSION
from timecapsulesmb.identity import load_install_identity, set_telemetry_enabled


BACKGROUND = "#202526"
SIDEBAR = "#191d1e"
PANEL = "#2c3234"
TEXT = "#f1f4f4"
MUTED = "#aab4b8"
BLUE = "#116dea"


def configure_tcl():
    if sys.platform == "win32" and not getattr(sys, "frozen", False):
        tcl = Path(sys.base_prefix) / "tcl"
        for variable, folder in (("TCL_LIBRARY", f"tcl{tk.TclVersion}"), ("TK_LIBRARY", f"tk{tk.TkVersion}")):
            path = tcl / folder
            if path.is_dir():
                os.environ.setdefault(variable, str(path))


class Desktop:
    def __init__(self, root: tk.Tk, state: Path):
        self.root = root
        self.state = state
        self.profiles_path = state / "devices.json"
        self.devices = self._load_profiles()
        self.passwords: dict[str, str] = {}
        self.events: Queue = Queue()
        self.busy = False
        self.discovered: list[dict] = []
        self.plan_device: str | None = None
        self.buttons: list[ttk.Widget] = []
        self.device_status = tk.StringVar(value="Select or add an AirPort device.")
        self.status = tk.StringVar(value="Ready")
        self.plan_status = tk.StringVar(value="Create a plan before installing or updating Samba.")
        self.telemetry = tk.BooleanVar(value=load_install_identity(state / ".bootstrap").telemetry_enabled)
        root.title("TimeCapsuleSMB")
        root.geometry("1040x690")
        root.minsize(830, 570)
        root.configure(bg=BACKGROUND)
        root.protocol("WM_DELETE_WINDOW", self._close)
        self._style()
        self._layout()
        self._refresh_devices()
        root.after(75, self._poll)

    def _style(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", background=BACKGROUND, foreground=TEXT, font=("Segoe UI", 10),
                        bordercolor=PANEL, fieldbackground=PANEL, lightcolor=PANEL, darkcolor=PANEL)
        style.configure("Title.TLabel", font=("Segoe UI Semibold", 17))
        style.configure("Section.TLabel", font=("Segoe UI Semibold", 13))
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("Side.TFrame", background=SIDEBAR)
        style.configure("Side.TLabel", background=SIDEBAR, foreground=MUTED, font=("Segoe UI Semibold", 10))
        style.configure("TButton", padding=(12, 7), background=PANEL)
        style.map("TButton", background=[("active", "#394246")], foreground=[("disabled", "#92999c")])
        style.configure("Primary.TButton", background=BLUE, foreground="#ffffff")
        style.map("Primary.TButton", background=[("disabled", PANEL), ("active", "#217eff")])
        style.configure("TNotebook", borderwidth=0, tabmargins=(0, 1, 0, 0))
        style.configure("TNotebook.Tab", padding=(13, 7), background=PANEL)
        style.map("TNotebook.Tab", background=[("selected", BLUE)], foreground=[("selected", "#ffffff")])
        style.map("TCombobox", fieldbackground=[("readonly", PANEL)], foreground=[("readonly", TEXT)],
                  selectbackground=[("readonly", PANEL)], selectforeground=[("readonly", TEXT)])
        self.root.option_add("*TCombobox*Listbox.background", PANEL)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        style.configure("Treeview", background=SIDEBAR, fieldbackground=SIDEBAR, rowheight=33, borderwidth=0)
        style.map("Treeview", background=[("selected", "#384449")], foreground=[("selected", "#ffffff")])

    def _button(self, parent, label, action, *, primary=False, side="left"):
        button = ttk.Button(parent, text=label, command=action, style="Primary.TButton" if primary else "TButton")
        button.pack(side=side, padx=(0, 7), pady=3)
        self.buttons.append(button)
        return button

    def _layout(self):
        sidebar = ttk.Frame(self.root, width=278, padding=(18, 20), style="Side.TFrame")
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        ttk.Label(sidebar, text="AIRPORT DEVICES", style="Side.TLabel").pack(anchor="w", pady=(0, 10))
        row = ttk.Frame(sidebar, style="Side.TFrame")
        row.pack(fill="x")
        self._button(row, "Discover", self._discover)
        self._button(row, "+ Add Device", self._add_device)
        self.interface_addresses = [
            (f"{address.ip} · {adapter.nice_name}", address.ip)
            for adapter in get_adapters() for address in adapter.ips
            if address.is_IPv4 and not ipaddress.IPv4Address(address.ip).is_loopback
        ]
        self.interface = ttk.Combobox(
            sidebar, state="readonly", values=["All network adapters", *(label for label, _ in self.interface_addresses)],
        )
        self.interface.current(0)
        self.interface.pack(fill="x", pady=(6, 0))
        self.tree = ttk.Treeview(sidebar, show="tree", selectmode="browse")
        self.tree.column("#0", width=232, stretch=True)
        self.tree.pack(fill="both", expand=True, pady=(15, 10))
        self.tree.bind("<<TreeviewSelect>>", self._selected)
        self._button(sidebar, "Activity", lambda: self.activity.focus_set(), side="bottom")

        body = ttk.Frame(self.root, padding=(22, 17))
        body.pack(side="left", fill="both", expand=True)
        ttk.Label(body, text="TimeCapsuleSMB", style="Title.TLabel").pack(anchor="w")
        ttk.Label(body, textvariable=self.device_status, style="Muted.TLabel").pack(anchor="w", pady=(3, 14))
        tabs = ttk.Notebook(body)
        tabs.pack(fill="both", expand=True)
        overview = self._page(tabs, "Overview", "Device overview", "Check connectivity or open the share in File Explorer.")
        self._button(overview, "Check connection", lambda: self._device_operation("reachability"))
        self._button(overview, "Open in Explorer", self._explore)
        install = self._page(tabs, "Install / Update", "Install / Update", self.plan_status)
        self._button(install, "Create installation plan", self._plan, primary=True)
        self.install_button = self._button(install, "Install / Update", self._install)
        checkup = self._page(tabs, "Checkup", "Device checkup", "Check SSH, Bonjour, SMB login and share file operations.")
        self._button(checkup, "Run checkup", lambda: self._device_operation("doctor"), primary=True)
        maintenance = self._page(tabs, "Maintenance", "Maintenance", "Start Samba after a reboot or remove the managed installation.")
        self._button(maintenance, "Activate Samba", lambda: self._device_operation("activate"))
        self._button(maintenance, "Uninstall Samba", lambda: self._device_operation("uninstall"))
        settings = self._page(tabs, "Settings", "Settings", f"TimeCapsuleSMB {CLI_VERSION} · Windows desktop")
        self._button(settings, "Validate installation", lambda: self._run("validate-install", {}))
        self._button(settings, "Forget device", self._forget)
        check = ttk.Checkbutton(settings.master, text="Send project diagnostics", variable=self.telemetry,
                                command=self._toggle_telemetry)
        check.pack(anchor="w", pady=7)
        self.buttons.append(check)

        ttk.Label(body, text="Activity", style="Section.TLabel").pack(anchor="w", pady=(12, 5))
        log_panel = ttk.Frame(body)
        log_panel.pack(fill="both", expand=True)
        self.activity = tk.Text(log_panel, height=11, wrap="word", bg=SIDEBAR, fg=TEXT,
                                insertbackground=TEXT, relief="flat", padx=10, pady=7,
                                font=("Consolas", 10), state="disabled")
        scroll = ttk.Scrollbar(log_panel, command=self.activity.yview)
        self.activity.configure(yscrollcommand=scroll.set)
        self.activity.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        ttk.Label(body, textvariable=self.status, style="Muted.TLabel").pack(anchor="w", pady=(8, 0))

    @staticmethod
    def _page(tabs, title, heading, description):
        page = ttk.Frame(tabs, padding=(15, 13))
        tabs.add(page, text=title)
        ttk.Label(page, text=heading, style="Section.TLabel").pack(anchor="w")
        kwargs = {"text": description} if isinstance(description, str) else {"textvariable": description}
        ttk.Label(page, **kwargs, style="Muted.TLabel", wraplength=510).pack(anchor="w", pady=(4, 9))
        row = ttk.Frame(page)
        row.pack(fill="x")
        return row

    def _load_profiles(self):
        try:
            data = json.loads(self.profiles_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        if not isinstance(data, dict):
            raise ValueError("Invalid device profile file.")
        for key, profile in data.items():
            if (len(key) != 32 or any(char not in "0123456789abcdef" for char in key)
                    or not isinstance(profile, dict)
                    or not all(isinstance(profile.get(field), str) for field in ("name", "host", "airport_mac"))):
                raise ValueError("Invalid saved device profile.")
        return data

    def _save_profiles(self):
        temporary = self.profiles_path.with_name(f"devices-{uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps(self.devices, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(self.profiles_path)
        finally:
            temporary.unlink(missing_ok=True)

    def _config(self, key):
        return str(self.state / f"{key}.env")

    def _refresh_devices(self, key=None):
        for item in self.tree.get_children():
            self.tree.delete(item)
        for device_id, device in self.devices.items():
            self.tree.insert("", "end", iid=device_id, text=device["name"])
        if self.devices:
            self.tree.selection_set(key or next(iter(self.devices)))
        self._selected()

    def _device_id(self):
        selected = self.tree.selection()
        return selected[0] if selected else None

    def _selected(self, _event=None):
        key = self._device_id()
        if key:
            device = self.devices[key]
            self.device_status.set(f"{device['name']}   •   {endpoint_host(device['host'])}")
        else:
            self.device_status.set("Select or add an AirPort device.")
        self.plan_status.set("Plan ready. Review the details in Activity." if key and key == self.plan_device
                             else "Create a plan before installing or updating Samba.")
        self.install_button.state(["!disabled"] if key and key == self.plan_device and not self.busy else ["disabled"])

    def _log(self, message):
        self.activity.configure(state="normal")
        self.activity.insert("end", f"{message}\n")
        self.activity.see("end")
        self.activity.configure(state="disabled")

    def _run(self, operation, params, done=None):
        if self.busy:
            return
        self.busy = True
        self.status.set(f"{operation.replace('-', ' ').capitalize()} in progress…")
        for button in self.buttons:
            button.state(["disabled"])

        def work():
            try:
                run_api_request({"operation": operation, "params": params}, EventSink(
                    lambda event: self.events.put(("event", event.to_jsonable())),
                ))
            except Exception as exc:
                self.events.put(("event", {"type": "error", "message": f"{type(exc).__name__}: {exc}"}))
            finally:
                self.events.put(("done", None))

        self.current = (operation, params, done)
        self.result = None
        self.confirmation = None
        self._log(f"— {operation.replace('-', ' ').capitalize()} —")
        Thread(target=work, daemon=True).start()

    def _poll(self):
        try:
            for _ in range(150):
                kind, value = self.events.get_nowait()
                if kind == "done":
                    self._finish()
                else:
                    self._event(value)
        except Empty:
            pass
        self.root.after(75, self._poll)

    def _event(self, event):
        event_type = event["type"]
        message = event.get("message")
        if event_type == "stage":
            self.status.set(str(event["stage"]).replace("_", " ").capitalize() + "…")
        elif event_type == "result":
            self.result = event
        elif event_type == "error" and event.get("code") == "confirmation_required":
            self.confirmation = event.get("details")
        elif message:
            status = event.get("status") or ("ERROR" if event_type == "error" else "")
            self._log(f"{status}  {message}".strip())
            if event.get("code") == "auth_failed":
                config = self.current[1].get("config")
                for key in self.devices:
                    if config == self._config(key):
                        self.passwords.pop(key, None)

    def _finish(self):
        operation, params, done = self.current
        self.current = None
        self.busy = False
        for button in self.buttons:
            button.state(["!disabled"])
        self._selected()
        if self.confirmation:
            item = self.confirmation
            if messagebox.askokcancel(item["title"], item["message"], icon="warning", default="cancel", parent=self.root):
                self._run(operation, {**params, "confirmation_id": item["confirmation_id"]}, done)
            else:
                self.status.set("Cancelled before approval.")
                self._log("Operation cancelled before approval.")
            return
        result = self.result or {}
        payload = result.get("payload") or {}
        self.status.set("Completed" if result.get("ok") else "Needs attention · see Activity")
        if payload.get("summary"):
            self._log(payload["summary"])
        if done and result.get("ok"):
            try:
                done(payload)
            except (OSError, ValueError) as exc:
                self.status.set("Needs attention · see Activity")
                self._log(f"ERROR  {exc}")

    def _discover(self):
        def found(payload):
            self.discovered = [device for device in payload.get("devices", []) if device.get("host")]
            self._log(f"Found {len(self.discovered)} AirPort device(s).")
            self._add_device()

        index = self.interface.current()
        params = {"interface": self.interface_addresses[index - 1][1]} if index > 0 else {}
        self._run("discover", params, found)

    def _add_device(self):
        dialog = AddDeviceDialog(self.root, self.discovered)
        if dialog.result is None:
            return
        host, password, discovered = dialog.result
        key = uuid4().hex
        params = {"host": host, "password": password, "config": self._config(key), "persist_password": False,
                  "saved_airport_macs": [device["airport_mac"] for device in self.devices.values() if device["airport_mac"]]}
        if discovered:
            params.pop("host")
            params["selected_record"] = discovered["selected_record"]

        def configured(payload):
            self.devices[key] = {
                "name": (discovered or {}).get("name") or payload.get("device_model") or host,
                "host": payload["host"],
                "airport_mac": payload.get("airport_mac") or "",
            }
            self._save_profiles()
            self.passwords[key] = password
            self._refresh_devices(key)

        self._run("configure", params, configured)

    def _device_operation(self, operation, *, key=None, options=None, done=None):
        key = key or self._device_id()
        if key is None:
            messagebox.showinfo("Select a device", "Discover or add an AirPort device first.", parent=self.root)
            return
        device = self.devices[key]
        password = self.passwords.get(key) or simpledialog.askstring(
            "Device password", f"Enter the AirPort password for {device['name']}.", show="•", parent=self.root,
        )
        if not password:
            return
        self.passwords[key] = password
        params = {"config": self._config(key), "password": password, **(options or {})}
        if device["airport_mac"]:
            params["airport_mac"] = device["airport_mac"]
        self._run(operation, params, done)

    def _plan(self):
        key = self._device_id()
        self.plan_device = None
        self._selected()

        def ready(payload):
            self._log(f"Payload: {payload.get('payload_family') or 'Samba'}")
            self._log(f"Reboot required: {'Yes' if payload.get('requires_reboot') else 'No'}")
            for transfer in payload.get("uploads", []):
                destination = transfer.get("destination")
                if destination:
                    self._log(f"  {destination}")
            self.plan_device = key
            self._selected()

        self._device_operation("deploy", key=key, options={"dry_run": True}, done=ready)

    def _install(self):
        key = self._device_id()
        if key and key == self.plan_device:
            self.plan_device = None
            self._selected()
            self._device_operation("deploy", key=key)

    def _explore(self):
        key = self._device_id()
        if key:
            try:
                os.startfile(unc_root(endpoint_host(self.devices[key]["host"])))
            except OSError as exc:
                self._log(f"ERROR  {exc}")

    def _forget(self):
        key = self._device_id()
        if not key or not messagebox.askokcancel("Forget device?", "Remove this saved device profile?",
                                                 default="cancel", parent=self.root):
            return
        device = self.devices.pop(key)
        try:
            self._save_profiles()
        except OSError as exc:
            self.devices[key] = device
            self._log(f"ERROR  {exc}")
            return
        self.passwords.pop(key, None)
        try:
            Path(self._config(key)).unlink(missing_ok=True)
        except OSError as exc:
            self._log(f"ERROR  {exc}")
        if self.plan_device == key:
            self.plan_device = None
        self._refresh_devices()

    def _toggle_telemetry(self):
        try:
            set_telemetry_enabled(self.telemetry.get(), self.state / ".bootstrap")
        except OSError as exc:
            self.telemetry.set(not self.telemetry.get())
            self._log(f"ERROR  {exc}")

    def _close(self):
        if self.busy:
            messagebox.showinfo("Operation in progress", "Wait for the current operation to finish.", parent=self.root)
        else:
            self.root.destroy()


class AddDeviceDialog(simpledialog.Dialog):
    def __init__(self, parent, devices):
        self.devices = devices
        super().__init__(parent, "Add AirPort Device")

    def body(self, master):
        self.configure(bg=BACKGROUND)
        master.configure(bg=BACKGROUND)
        ttk.Label(master, text="Discovered device").grid(row=0, column=0, sticky="w", pady=5)
        self.device = ttk.Combobox(master, state="readonly", width=42, values=[
            "Enter an address manually", *(f"{item['name']}  ·  {item['host']}" for item in self.devices),
        ])
        self.device.current(1 if self.devices else 0)
        self.device.grid(row=0, column=1, padx=9)
        self.device.bind("<<ComboboxSelected>>", self._pick)
        ttk.Label(master, text="Host or IP address").grid(row=1, column=0, sticky="w", pady=5)
        self.host = ttk.Entry(master, width=44)
        self.host.grid(row=1, column=1, padx=9)
        ttk.Label(master, text="Device password").grid(row=2, column=0, sticky="w", pady=5)
        self.password = ttk.Entry(master, width=44, show="•")
        self.password.grid(row=2, column=1, padx=9)
        self._pick()
        return self.password if self.devices else self.host

    def buttonbox(self):
        buttons = ttk.Frame(self, padding=(10, 6))
        ttk.Button(buttons, text="Add Device", command=self.ok, style="Primary.TButton").pack(side="left", padx=4)
        ttk.Button(buttons, text="Cancel", command=self.cancel).pack(side="left", padx=4)
        self.bind("<Return>", self.ok)
        self.bind("<Escape>", self.cancel)
        buttons.pack()

    def _pick(self, _event=None):
        index = self.device.current()
        self.host.state(["!readonly"])
        self.host.delete(0, "end")
        if index > 0:
            self.host.insert(0, self.devices[index - 1]["host"])
            self.host.state(["readonly"])

    def validate(self):
        if not self.host.get().strip() or not self.password.get():
            messagebox.showinfo("Missing device details", "Enter a host and the AirPort password.", parent=self)
            return False
        return True

    def apply(self):
        index = self.device.current()
        self.result = (self.host.get().strip(), self.password.get(), self.devices[index - 1] if index > 0 else None)


def main():
    configure_tcl()
    root = tk.Tk()
    try:
        if sys.platform != "win32":
            raise OSError("This desktop entry point is for Windows.")
        state = default_user_data_dir() / "Desktop"
        state.mkdir(parents=True, exist_ok=True)
        os.environ["TCAPSULE_STATE_DIR"] = str(state)
        os.environ["TCAPSULE_CONFIG"] = str(state / "default.env")
        if getattr(sys, "frozen", False):
            os.environ["TCAPSULE_DISTRIBUTION_ROOT"] = str(Path(sys.executable).resolve().parent)
        if not (state / ".bootstrap").exists():
            set_telemetry_enabled(False, state / ".bootstrap")
        Desktop(root, state)
    except (OSError, ValueError, RuntimeError) as exc:
        root.withdraw()
        messagebox.showerror("TimeCapsuleSMB", str(exc), parent=root)
        root.destroy()
        return 1
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
