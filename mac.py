"""
Blast Radio Controller -- macOS edition

Install dependencies:
    pip install numpy pyautogui pillow pyobjc-framework-Quartz pyobjc-framework-AppKit

Grant permissions in System Settings > Privacy & Security:
    - Screen Recording  : screenshot-based signal detection
    - Accessibility     : simulated mouse clicks
"""

import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import json
import os
import subprocess

import numpy as np
import pyautogui

pyautogui.FAILSAFE = False

# --------------------------------------------------------------------------
# PyObjC frameworks
# pip install pyobjc-framework-Quartz pyobjc-framework-AppKit
# --------------------------------------------------------------------------
try:
    import Quartz
    _QUARTZ = True
except ImportError:
    _QUARTZ = False

try:
    from AppKit import NSRunningApplication, NSApplicationActivateIgnoringOtherApps
    _APPKIT = True
except ImportError:
    _APPKIT = False


# --------------------------------------------------------------------------
# macOS window helpers
# --------------------------------------------------------------------------

def _run_osascript(script: str) -> str:
    """Execute a one-liner AppleScript and return its output (or empty string)."""
    try:
        r = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=3
        )
        return r.stdout.strip()
    except Exception:
        return ""


class MacOSWindow:
    """
    Lightweight replacement for pygetwindow's Window object.
    Data is sourced from the Quartz CGWindowList.
    Window actions are performed through AppKit (preferred) or AppleScript.
    """

    __slots__ = ("title", "left", "top", "width", "height", "isMinimized", "_pid")

    def __init__(self, *, name, left, top, width, height, pid=None, is_minimized=False):
        self.title       = name
        self.left        = left
        self.top         = top
        self.width       = width
        self.height      = height
        self.isMinimized = is_minimized
        self._pid        = pid

    def activate(self):
        """Bring the application window to the foreground."""
        if _APPKIT and self._pid:
            try:
                app = NSRunningApplication.runningApplicationWithProcessIdentifier_(self._pid)
                if app:
                    app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
                    return
            except Exception:
                pass
        _run_osascript(f'tell application "{self.title}" to activate')

    def restore(self):
        """Un-minimize the window."""
        if self._pid:
            _run_osascript(
                f'tell application "System Events" to set miniaturized of '
                f'windows of (first process whose unix id is {self._pid}) to false'
            )
        else:
            _run_osascript(
                f'tell application "System Events" to set miniaturized of '
                f'windows of (first process whose name is "{self.title}") to false'
            )


def _cg_list(on_screen_only: bool) -> list:
    """Return the Quartz window-info list, or [] when Quartz is unavailable."""
    if not _QUARTZ:
        return []
    flags = Quartz.kCGWindowListExcludeDesktopElements
    if on_screen_only:
        flags |= Quartz.kCGWindowListOptionOnScreenOnly
    return Quartz.CGWindowListCopyWindowInfo(flags, Quartz.kCGNullWindowID) or []


def find_app_window(app_title: str):
    """
    Search for a window whose owner process name or window title contains
    *app_title* (case-insensitive), excluding windows with "controller" in
    their title.

    On-screen windows are checked first (not minimized); the fallback pass
    searches all windows so minimized applications are still detected.

    Returns a MacOSWindow or None.

    NOTE: Update APP_TITLE in BlastRadioController if the Blast Radio process
    on your system uses a different name (check Activity Monitor > Process Name).
    """
    needle = app_title.lower()

    for is_minimized, win_list in [
        (False, _cg_list(on_screen_only=True)),
        (True,  _cg_list(on_screen_only=False)),
    ]:
        for entry in win_list:
            owner  = (entry.get("kCGWindowOwnerName") or "").lower()
            wtitle = (entry.get("kCGWindowName")      or "").lower()
            layer  = entry.get("kCGWindowLayer", 0)

            # Layer 0 = normal app windows; <= 5 also covers floating panels.
            # Higher layers are the Dock (8+), menu bar (24+), etc.
            if layer > 5:
                continue
            if needle not in owner and needle not in wtitle:
                continue
            if "controller" in wtitle:
                continue

            bd = entry.get("kCGWindowBounds", {})
            return MacOSWindow(
                name         = entry.get("kCGWindowOwnerName", app_title),
                left         = int(bd.get("X",      0)),
                top          = int(bd.get("Y",      0)),
                width        = int(bd.get("Width",  0)),
                height       = int(bd.get("Height", 0)),
                pid          = entry.get("kCGWindowOwnerPID"),
                is_minimized = is_minimized,
            )
    return None


# --------------------------------------------------------------------------
# Controller
# --------------------------------------------------------------------------

class BlastRadioController:

    BG       = "#0f0f1a"
    PANEL    = "#16213e"
    ACCENT   = "#e94560"
    GREEN    = "#4ecca3"
    YELLOW   = "#f5a623"
    TEXT     = "#e0e0e0"
    DIM      = "#667788"
    ENTRY_BG = "#0d1b2a"

    # Must match the process name shown in Activity Monitor for your app.
    APP_TITLE   = "Blast Radio"
    CONFIG_FILE = "blast_radio_config.json"

    def __init__(self, root):
        self.root = root
        self.root.title("Blast Radio Controller")
        self.root.minsize(520, 800)
        self.root.resizable(True, True)
        self.root.configure(bg=self.BG)

        self._ui_ready = False

        self.is_broadcasting  = False
        self.broadcast_locked = False
        self.silence_start    = None
        self.broadcast_start  = None
        self.current_signal   = 0.0

        self.window_visible = False
        self.start_coords   = None
        self.stop_coords    = None
        self.signal_region  = None

        self.saved_threshold   = 5.0
        self.saved_silence_sec = 30
        self.saved_auto_start  = True
        self.saved_auto_stop   = True
        self.saved_app_path    = ""
        self.saved_region_w    = 20
        self.saved_region_h    = 80

        self._load_config()

        self._threshold   = self.saved_threshold
        self._silence_sec = self.saved_silence_sec
        self._auto_start  = self.saved_auto_start
        self._auto_stop   = self.saved_auto_stop

        self.threshold_var    = tk.DoubleVar(value=self.saved_threshold)
        self.silence_sec_var  = tk.IntVar(value=self.saved_silence_sec)
        self.auto_start_var   = tk.BooleanVar(value=self.saved_auto_start)
        self.auto_stop_var    = tk.BooleanVar(value=self.saved_auto_stop)
        self.app_path_var     = tk.StringVar(value=self.saved_app_path)
        self.region_w_var     = tk.IntVar(value=self.saved_region_w)
        self.region_h_var     = tk.IntVar(value=self.saved_region_h)
        self.show_overlay_var = tk.BooleanVar(value=True)

        self.threshold_var.trace_add("write",   lambda *_: self._on_setting_change("threshold"))
        self.silence_sec_var.trace_add("write", lambda *_: self._on_setting_change("silence"))
        self.auto_start_var.trace_add("write",  lambda *_: self._on_setting_change("auto_start"))
        self.auto_stop_var.trace_add("write",   lambda *_: self._on_setting_change("auto_stop"))
        self.app_path_var.trace_add("write",    lambda *_: self._on_setting_change("app_path"))
        self.region_w_var.trace_add("write",    lambda *_: self._save_config())
        self.region_h_var.trace_add("write",    lambda *_: self._save_config())

        self._setup_styles()
        self._build_ui()
        self._create_overlay()

        self._ui_ready = True

        if not _QUARTZ:
            self.root.after(800, lambda: self._log(
                "WARNING: pyobjc-framework-Quartz not found. "
                "Window detection is disabled. "
                "Fix: pip install pyobjc-framework-Quartz"
            ))

        threading.Thread(target=self._screen_monitor_thread, daemon=True).start()
        threading.Thread(target=self._window_watcher,        daemon=True).start()
        self._ui_loop()

    # --- CONFIG -----------------------------------------------------------

    def _load_config(self):
        if not os.path.exists(self.CONFIG_FILE):
            return
        try:
            with open(self.CONFIG_FILE) as f:
                data = json.load(f)
            if data.get("start_coords"):
                self.start_coords  = tuple(data["start_coords"])
            if data.get("stop_coords"):
                self.stop_coords   = tuple(data["stop_coords"])
            if data.get("signal_region"):
                self.signal_region = tuple(data["signal_region"])
            for json_key, attr in [
                ("threshold",   "saved_threshold"),
                ("silence_sec", "saved_silence_sec"),
                ("auto_start",  "saved_auto_start"),
                ("auto_stop",   "saved_auto_stop"),
                ("app_path",    "saved_app_path"),
                ("region_w",    "saved_region_w"),
                ("region_h",    "saved_region_h"),
            ]:
                if json_key in data:
                    setattr(self, attr, data[json_key])
        except Exception as e:
            print(f"Config load error: {e}")

    def _save_config(self):
        if not self._ui_ready:
            return
        data = {
            "start_coords":  self.start_coords,
            "stop_coords":   self.stop_coords,
            "signal_region": list(self.signal_region) if self.signal_region else None,
            "threshold":     self.threshold_var.get(),
            "silence_sec":   self.silence_sec_var.get(),
            "auto_start":    self.auto_start_var.get(),
            "auto_stop":     self.auto_stop_var.get(),
            "app_path":      self.app_path_var.get(),
            "region_w":      self.region_w_var.get(),
            "region_h":      self.region_h_var.get(),
        }
        try:
            with open(self.CONFIG_FILE, "w") as f:
                json.dump(data, f)
        except Exception as e:
            print(f"Config save error: {e}")

    def _on_setting_change(self, setting):
        if   setting == "threshold":  self._threshold   = self.threshold_var.get()
        elif setting == "silence":    self._silence_sec = self.silence_sec_var.get()
        elif setting == "auto_start": self._auto_start  = self.auto_start_var.get()
        elif setting == "auto_stop":  self._auto_stop   = self.auto_stop_var.get()
        self._save_config()

    def _browse_app(self):
        # .app bundles are directories on macOS; askdirectory shows them correctly
        # in the native file-chooser sheet.
        path = filedialog.askdirectory(title="Select Blast Radio .app Bundle")
        if path:
            self.app_path_var.set(path)

    # --- STYLES & UI ------------------------------------------------------

    def _setup_styles(self):
        s = ttk.Style()
        s.theme_use("clam")
        s.configure("TSpinbox",
                    fieldbackground=self.ENTRY_BG, foreground=self.TEXT,
                    background=self.ENTRY_BG)
        s.configure("TCheckbutton",
                    background=self.PANEL, foreground=self.TEXT, focuscolor=self.PANEL)

    def _build_ui(self):
        hdr = tk.Frame(self.root, bg=self.ACCENT, height=54)
        hdr.pack(fill=tk.X)
        hdr.pack_propagate(False)
        tk.Label(hdr, text="BLAST RADIO CONTROLLER",
                 font=("Helvetica", 13, "bold"),
                 bg=self.ACCENT, fg="white").pack(expand=True)

        wrap = tk.Frame(self.root, bg=self.BG, padx=16, pady=12)
        wrap.pack(fill=tk.BOTH, expand=True)

        self._panel_setup(wrap)
        self._panel_signal(wrap)
        self._panel_automation(wrap)
        self._panel_controls(wrap)
        self._panel_log(wrap)

    def _section(self, parent, title):
        tk.Label(parent, text=title, font=("Helvetica", 8, "bold"),
                 bg=self.BG, fg=self.DIM).pack(anchor=tk.W, pady=(0, 3))
        inner = tk.Frame(parent, bg=self.PANEL, padx=14, pady=10)
        inner.pack(fill=tk.X, pady=(0, 10))
        return inner

    # 1. SETUP
    def _panel_setup(self, p):
        f = self._section(p, "1. WINDOW & BUTTON SETUP")

        path_row = tk.Frame(f, bg=self.PANEL)
        path_row.pack(fill=tk.X, pady=(0, 10))
        tk.Label(path_row, text="App Path:", bg=self.PANEL, fg=self.TEXT).pack(side=tk.LEFT)
        tk.Entry(path_row, textvariable=self.app_path_var,
                 bg=self.ENTRY_BG, fg=self.TEXT, width=30).pack(side=tk.LEFT, padx=5)
        # On macOS, .app bundles are chosen with askdirectory (see _browse_app)
        tk.Button(path_row, text="Browse .app", bg=self.ENTRY_BG, fg=self.TEXT,
                  relief=tk.FLAT, command=self._browse_app).pack(side=tk.LEFT)

        self.win_status_lbl = tk.Label(
            f, text="Checking for Blast Radio window...",
            bg=self.PANEL, fg=self.YELLOW, font=("Helvetica", 10, "bold"))
        self.win_status_lbl.pack(anchor=tk.W, pady=(0, 10))

        btn_frame = tk.Frame(f, bg=self.PANEL)
        btn_frame.pack(fill=tk.X)

        st  = (f"Start Button: Rel X={self.start_coords[0]}, Y={self.start_coords[1]}"
               if self.start_coords else "Start Button: Not Set")
        spt = (f"Stop Button: Rel X={self.stop_coords[0]}, Y={self.stop_coords[1]}"
               if self.stop_coords  else "Stop Button: Not Set")

        self.start_btn_lbl = tk.Label(
            btn_frame, text=st, bg=self.PANEL,
            fg=self.GREEN if self.start_coords else self.DIM)
        self.start_btn_lbl.grid(row=0, column=0, sticky=tk.W, pady=5)
        tk.Button(btn_frame, text="Pick Start", bg=self.ENTRY_BG, fg=self.TEXT,
                  relief=tk.FLAT,
                  command=lambda: self._pick_position("start")).grid(row=0, column=1, padx=10)

        self.stop_btn_lbl = tk.Label(
            btn_frame, text=spt, bg=self.PANEL,
            fg=self.GREEN if self.stop_coords else self.DIM)
        self.stop_btn_lbl.grid(row=1, column=0, sticky=tk.W, pady=5)
        tk.Button(btn_frame, text="Pick Stop", bg=self.ENTRY_BG, fg=self.TEXT,
                  relief=tk.FLAT,
                  command=lambda: self._pick_position("stop")).grid(row=1, column=1, padx=10)

    # 2. SIGNAL
    def _panel_signal(self, p):
        f = self._section(p, "2. SIGNAL DETECTION (SCREEN CAPTURE)")

        tk.Label(f,
                 text=("Hover over the TOP-LEFT corner of the yellow signal bars, "
                       "then click Pick Signal Region."),
                 bg=self.PANEL, fg=self.DIM, font=("Helvetica", 8),
                 wraplength=440, justify=tk.LEFT).pack(anchor=tk.W, pady=(0, 6))

        top_row = tk.Frame(f, bg=self.PANEL)
        top_row.pack(fill=tk.X, pady=(0, 6))

        region_text = (f"Region: Rel X={self.signal_region[0]}, Y={self.signal_region[1]}"
                       if self.signal_region else "Signal Region: Not Set")
        self._region_lbl = tk.Label(
            top_row, text=region_text, bg=self.PANEL,
            fg=self.GREEN if self.signal_region else self.DIM)
        self._region_lbl.pack(side=tk.LEFT)

        self._signal_val_lbl = tk.Label(
            top_row, text="0.0%", bg=self.PANEL,
            fg=self.ACCENT, font=("Helvetica", 10, "bold"))
        self._signal_val_lbl.pack(side=tk.RIGHT)

        ctrl_row = tk.Frame(f, bg=self.PANEL)
        ctrl_row.pack(fill=tk.X, pady=(0, 8))

        tk.Button(ctrl_row, text="Pick Signal Region", bg=self.ENTRY_BG, fg=self.TEXT,
                  relief=tk.FLAT, command=self._pick_signal_region).pack(side=tk.LEFT)

        tk.Label(ctrl_row, text="  W:", bg=self.PANEL, fg=self.TEXT).pack(side=tk.LEFT)
        ttk.Spinbox(ctrl_row, from_=5, to=300, increment=5,
                    textvariable=self.region_w_var, width=5).pack(side=tk.LEFT)

        tk.Label(ctrl_row, text="  H:", bg=self.PANEL, fg=self.TEXT).pack(side=tk.LEFT)
        ttk.Spinbox(ctrl_row, from_=5, to=300, increment=5,
                    textvariable=self.region_h_var, width=5).pack(side=tk.LEFT)

        ttk.Checkbutton(ctrl_row, text="  Show overlay on screen",
                        variable=self.show_overlay_var).pack(side=tk.LEFT, padx=(10, 0))

        tk.Label(f, text="Yellow pixel ratio  (white dashed line = threshold)",
                 bg=self.PANEL, fg=self.DIM, font=("Helvetica", 8)).pack(anchor=tk.W)

        self._signal_canvas = tk.Canvas(f, height=22, bg=self.PANEL, highlightthickness=0)
        self._signal_canvas.pack(fill=tk.X, pady=(2, 10))

        row = tk.Frame(f, bg=self.PANEL)
        row.pack(fill=tk.X)
        tk.Label(row, text="Activity Threshold (%)",
                 bg=self.PANEL, fg=self.TEXT).pack(side=tk.LEFT)
        self._thresh_lbl = tk.Label(
            row, text=f"{self.saved_threshold:.1f}%", width=6,
            bg=self.PANEL, fg=self.ACCENT, font=("Helvetica", 10, "bold"))
        self._thresh_lbl.pack(side=tk.RIGHT)
        tk.Scale(f,
                 from_=0.1, to=50.0, resolution=0.1, orient=tk.HORIZONTAL,
                 variable=self.threshold_var, bg=self.PANEL, fg=self.TEXT,
                 highlightthickness=0, troughcolor=self.ENTRY_BG,
                 activebackground=self.ACCENT, showvalue=False,
                 command=lambda v: self._thresh_lbl.config(text=f"{float(v):.1f}%")
                 ).pack(fill=tk.X)

    # 3. AUTOMATION
    def _panel_automation(self, p):
        f = self._section(p, "3. AUTOMATION SETTINGS")
        ttk.Checkbutton(
            f, text="Auto-start broadcast when signal crosses threshold",
            variable=self.auto_start_var).pack(anchor=tk.W, pady=(0, 5))
        ttk.Checkbutton(
            f, text="Auto-stop broadcast after signal drops below threshold",
            variable=self.auto_stop_var).pack(anchor=tk.W, pady=(0, 5))
        row = tk.Frame(f, bg=self.PANEL)
        row.pack(fill=tk.X, pady=(5, 0))
        tk.Label(row, text="Duration with no signal before stopping (seconds):",
                 bg=self.PANEL, fg=self.TEXT).pack(side=tk.LEFT)
        ttk.Spinbox(row, from_=5, to=600, increment=5,
                    textvariable=self.silence_sec_var, width=7).pack(side=tk.RIGHT)

    # 4. CONTROLS
    def _panel_controls(self, p):
        f = self._section(p, "4. CONTROLS & STATUS")
        top = tk.Frame(f, bg=self.PANEL)
        top.pack(fill=tk.X, pady=(0, 10))

        self._status_lbl = tk.Label(
            top, text="Idle", bg=self.PANEL, fg=self.DIM,
            font=("Helvetica", 11, "bold"))
        self._status_lbl.pack(side=tk.LEFT)

        self._timer_lbl = tk.Label(
            top, text="", bg=self.PANEL, fg=self.ACCENT,
                        font=("Helvetica", 11, "bold"))
        self._timer_lbl.pack(side=tk.LEFT, padx=(12, 0))

        self._silence_lbl = tk.Label(f, text="", bg=self.PANEL, fg=self.YELLOW,
                                     font=("Helvetica", 9))
        self._silence_lbl.pack(anchor=tk.W, pady=(0, 8))

        btn_row = tk.Frame(f, bg=self.PANEL)
        btn_row.pack(fill=tk.X)
        tk.Button(btn_row, text="START BROADCAST", font=("Helvetica", 10, "bold"),
                  bg=self.GREEN, fg="#0f0f1a", relief=tk.FLAT, padx=18, pady=9,
                  cursor="hand2", command=self._manual_start).pack(side=tk.LEFT, padx=(0, 10))
        tk.Button(btn_row, text="STOP BROADCAST", font=("Helvetica", 10, "bold"),
                  bg=self.ACCENT, fg="white", relief=tk.FLAT, padx=18, pady=9,
                  cursor="hand2", command=self._manual_stop).pack(side=tk.LEFT)

    # 5. LOG
    def _panel_log(self, p):
        f = self._section(p, "ACTIVITY LOG")
        wrap = tk.Frame(f, bg=self.PANEL)
        wrap.pack(fill=tk.BOTH, expand=True)

        self._log_box = tk.Text(wrap, height=6, bg="#090d15", fg=self.GREEN,
                                font=("Courier", 9), state=tk.DISABLED, wrap=tk.WORD,
                                relief=tk.FLAT, padx=8, pady=6)
        self._log_box.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        sb = tk.Scrollbar(wrap, command=self._log_box.yview, bg=self.PANEL,
                          troughcolor=self.ENTRY_BG, relief=tk.FLAT)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self._log_box.configure(yscrollcommand=sb.set)

    # --- POSITION PICKING -------------------------------------------------

    def _pick_position(self, target):
        self._log(f"Window minimized. Hover over the {target.upper()} button.")
        self.root.iconify()

        def capture():
            for i in range(5, 0, -1):
                self.root.after(0, self._log, f"Capturing in {i}...")
                time.sleep(1)
            win = self._get_blast_window()
            if not win:
                self.root.after(0, self._log, "Capture failed: Blast Radio window not found.")
                self.root.after(0, self.root.deiconify)
                return
            abs_x, abs_y = pyautogui.position()
            rel_x = abs_x - win.left
            rel_y = abs_y - win.top
            if target == "start":
                self.start_coords = (rel_x, rel_y)
                self.root.after(0, lambda: self.start_btn_lbl.config(
                    text=f"Start Button: Rel X={rel_x}, Y={rel_y}", fg=self.GREEN))
            else:
                self.stop_coords = (rel_x, rel_y)
                self.root.after(0, lambda: self.stop_btn_lbl.config(
                    text=f"Stop Button: Rel X={rel_x}, Y={rel_y}", fg=self.GREEN))
            self._save_config()
            self.root.after(0, self.root.deiconify)
            self.root.after(0, self._log, f"{target.capitalize()} button position saved.")

        threading.Thread(target=capture, daemon=True).start()

    def _pick_signal_region(self):
        self._log("Minimizing. Hover over the TOP-LEFT corner of the yellow bars, then wait.")
        self.root.iconify()

        def capture():
            for i in range(5, 0, -1):
                self.root.after(0, self._log, f"Capturing in {i}...")
                time.sleep(1)
            win = self._get_blast_window()
            if not win:
                self.root.after(0, self._log, "Capture failed: Blast Radio window not found.")
                self.root.after(0, self.root.deiconify)
                return
            abs_x, abs_y = pyautogui.position()
            rel_x = abs_x - win.left
            rel_y = abs_y - win.top
            self.signal_region = (rel_x, rel_y,
                                  self.region_w_var.get(),
                                  self.region_h_var.get())
            self.root.after(0, lambda: self._region_lbl.config(
                text=f"Region: Rel X={rel_x}, Y={rel_y}", fg=self.GREEN))
            self._save_config()
            self.root.after(0, self.root.deiconify)
            self.root.after(0, self._log,
                            "Signal region anchor saved. Use W/H spinboxes to resize the overlay "
                            "until it covers all the bars, then watch the percentage climb.")

        threading.Thread(target=capture, daemon=True).start()

    # --- WINDOW HELPERS ---------------------------------------------------

    def _get_blast_window(self):
        return find_app_window(self.APP_TITLE)

    def _window_watcher(self):
        while True:
            win = self._get_blast_window()
            if win and not win.isMinimized:
                self.window_visible = True
                self.root.after(0, lambda: self.win_status_lbl.config(
                    text="Blast Radio window is visible and ready.", fg=self.GREEN))
            else:
                self.window_visible = False
                self.root.after(0, lambda: self.win_status_lbl.config(
                    text="WARNING: Blast Radio window not found or minimized!", fg=self.ACCENT))
            time.sleep(1)

    def _ensure_window_ready(self):
        win = self._get_blast_window()
        if win:
            return win
        path = self.app_path_var.get()
        if path and os.path.exists(path):
            self._log("Launching Blast Radio...")
            try:
                # On macOS, use the 'open' command to launch .app bundles
                subprocess.Popen(["open", path])
                for _ in range(20):
                    time.sleep(0.5)
                    win = self._get_blast_window()
                    if win:
                        self._log("Blast Radio launched successfully.")
                        time.sleep(3)
                        return win
            except Exception as e:
                self._log(f"Failed to launch app: {e}")
        else:
            self._log("Cannot launch app: Path is invalid or not set.")
        return None

    def _safe_click(self, rel_coords, is_start=False):
        win = self._get_blast_window()
        if not win and is_start:
            win = self._ensure_window_ready()
        if not win:
            self._log("WARNING: Click aborted. Blast Radio window not found.")
            return False
        if win.isMinimized:
            win.restore()
        try:
            win.activate()
            time.sleep(0.5)
        except Exception:
            pass
        abs_x = win.left + rel_coords[0]
        abs_y = win.top  + rel_coords[1]
        try:
            pyautogui.click(abs_x, abs_y)
            return True
        except Exception as e:
            self._log(f"Click error: {e}")
            return False

    # --- OVERLAY ----------------------------------------------------------

    def _create_overlay(self):
        self._overlay = tk.Toplevel(self.root)
        self._overlay.overrideredirect(True)
        self._overlay.wm_attributes('-topmost', True)
        try:
            # macOS specific transparency
            self._overlay.wm_attributes('-transparent', True)
            self._overlay.config(bg='systemTransparent')
        except Exception:
            pass
        self._overlay.withdraw()

        self._overlay_canvas = tk.Canvas(
            self._overlay, bg='systemTransparent', highlightthickness=0
        )
        self._overlay_canvas.pack(fill=tk.BOTH, expand=True)
        self._overlay_last_geom = None

    def _update_overlay(self):
        if not self.show_overlay_var.get() or not self.signal_region:
            self._overlay.withdraw()
            self._overlay_last_geom = None
            return

        win = self._get_blast_window()
        if not win or win.isMinimized:
            self._overlay.withdraw()
            self._overlay_last_geom = None
            return

        rel_x, rel_y = self.signal_region[0], self.signal_region[1]
        rw = self.region_w_var.get()
        rh = self.region_h_var.get()

        abs_x = win.left + rel_x
        abs_y = win.top  + rel_y

        pad = 4
        lh  = 20

        ow = rw + pad * 2
        oh = rh + pad * 2 + lh

        geom = f"{ow}x{oh}+{abs_x - pad}+{abs_y - lh - pad}"
        if geom != self._overlay_last_geom:
            self._overlay.geometry(geom)
            self._overlay_last_geom = geom

        self._overlay.deiconify()
        self._draw_overlay(ow, oh, lh, pad, rw, rh)

    def _draw_overlay(self, ow, oh, lh, pad, rw, rh):
        c     = self._overlay_canvas
        color = self.GREEN if self.current_signal > self._threshold else self.ACCENT
        bw    = 2
        cm    = 10

        c.configure(width=ow, height=oh)
        c.delete("all")

        c.create_text(
            ow // 2, lh // 2,
            text=f"Signal: {self.current_signal:.1f}%  |  Threshold: {self._threshold:.1f}%",
            fill=color,
            font=("Helvetica", 8, "bold"),
            anchor='center'
        )

        bx1 = 0
        by1 = lh
        bx2 = ow
        by2 = oh

        c.create_rectangle(bx1,       by1,       bx2,       by1 + bw, fill=color, outline='')
        c.create_rectangle(bx1,       by2 - bw,  bx2,       by2,      fill=color, outline='')
        c.create_rectangle(bx1,       by1,       bx1 + bw,  by2,      fill=color, outline='')
        c.create_rectangle(bx2 - bw,  by1,       bx2,       by2,      fill=color, outline='')

        ct = bw + 1
        corners = [
            [(bx1, by1, bx1 + cm, by1 + ct), (bx1, by1, bx1 + ct, by1 + cm)],
            [(bx2 - cm, by1, bx2, by1 + ct), (bx2 - ct, by1, bx2, by1 + cm)],
            [(bx1, by2 - ct, bx1 + cm, by2), (bx1, by2 - cm, bx1 + ct, by2)],
            [(bx2 - cm, by2 - ct, bx2, by2), (bx2 - ct, by2 - cm, bx2, by2)],
        ]
        for pair in corners:
            for rect in pair:
                c.create_rectangle(*rect, fill=color, outline='')

    # --- SIGNAL DETECTION -------------------------------------------------

    def _measure_signal(self):
        if not self.signal_region:
            return 0.0
        win = self._get_blast_window()
        if not win or win.isMinimized:
            return 0.0

        rel_x, rel_y = self.signal_region[0], self.signal_region[1]
        w = self.region_w_var.get()
        h = self.region_h_var.get()
        if w < 1 or h < 1:
            return 0.0

        abs_x = win.left + rel_x
        abs_y = win.top  + rel_y

        try:
            img = pyautogui.screenshot(region=(abs_x, abs_y, w, h))
            arr = np.array(img)
            
            # On macOS Retina displays, the screenshot might be 2x the requested size.
            # We use the actual array shape to calculate the total pixels.
            actual_h, actual_w, _ = arr.shape
            total_pixels = actual_h * actual_w

            r = arr[:, :, 0].astype(np.int32)
            g = arr[:, :, 1].astype(np.int32)
            b = arr[:, :, 2].astype(np.int32)
            
            mask  = (r > 170) & (g > 100) & (b < 100) & ((r - b) > 100)
            count = float(np.sum(mask))
            
            return (count / total_pixels) * 100.0
        except Exception:
            return 0.0

    def _screen_monitor_thread(self):
        while True:
            self.current_signal = self._measure_signal()

            if self.current_signal > self._threshold:
                self.silence_start = None
                if self._auto_start and not self.is_broadcasting and not self.broadcast_locked:
                    self.root.after(0, self._do_start, "auto")
            else:
                if self.is_broadcasting and self._auto_stop:
                    if self.silence_start is None:
                        self.silence_start = time.time()
                    elif time.time() - self.silence_start >= self._silence_sec:
                        self.silence_start = None
                        self.root.after(0, self._do_stop, "auto")

            time.sleep(0.25)

    # --- BROADCAST CONTROL ------------------------------------------------

    def _do_start(self, mode="manual"):
        if self.is_broadcasting or self.broadcast_locked:
            return
        if not self.start_coords:
            self._log("Cannot start: Start button coordinates not set.")
            return
        self.broadcast_locked = True
        if self._safe_click(self.start_coords, is_start=True):
            self.is_broadcasting = True
            self.broadcast_start = time.time()
            self.silence_start   = None
            self._on_state_change(True)
            self._log(f"Broadcast started ({mode}).")
        self.root.after(2000, self._unlock)

    def _do_stop(self, mode="manual"):
        if not self.is_broadcasting or self.broadcast_locked:
            return
        if not self.stop_coords:
            self._log("Cannot stop: Stop button coordinates not set.")
            return
        self.broadcast_locked = True
        success = self._safe_click(self.stop_coords, is_start=False)
        if success:
            self.is_broadcasting = False
            self.broadcast_start = None
            self.silence_start   = None
            self._on_state_change(False)
            self._log(f"Broadcast stopped ({mode}).")
        else:
            force_stop = messagebox.askyesno(
                "Window Not Found",
                "Unable to find the process running. Do you still want to stop it manually?"
            )
            if force_stop:
                self.is_broadcasting = False
                self.broadcast_start = None
                self.silence_start   = None
                self._on_state_change(False)
                self._log(f"Broadcast stopped internally by user ({mode}).")
        self.root.after(2000, self._unlock)

    def _unlock(self):
        self.broadcast_locked = False

    def _manual_start(self):
        self._do_start("manual")

    def _manual_stop(self):
        self._do_stop("manual")

    def _on_state_change(self, live):
        if live:
            self._status_lbl.config(text="LIVE", fg=self.ACCENT)
        else:
            self._status_lbl.config(text="Idle", fg=self.DIM)
            self._timer_lbl.config(text="")
            self._silence_lbl.config(text="")

    # --- UI LOOP ----------------------------------------------------------

    def _ui_loop(self):
        self._draw_signal_bar()
        self._refresh_broadcast_panel()
        self._update_overlay()
        self.root.after(100, self._ui_loop)

    def _draw_signal_bar(self):
        c = self._signal_canvas
        c.update_idletasks()
        w = c.winfo_width()
        if w < 2:
            return
        c.delete("all")
        c.create_rectangle(0, 0, w, 22, fill=self.ENTRY_BG, outline="")

        pct = min(self.current_signal / 100.0, 1.0)
        bw  = int(pct * w)
        if bw > 0:
            color = self.GREEN if pct < 0.6 else self.YELLOW if pct < 0.85 else self.ACCENT
            c.create_rectangle(0, 3, bw, 19, fill=color, outline="")

        tp = min(self._threshold / 100.0, 1.0)
        tx = int(tp * w)
        c.create_line(tx, 0, tx, 22, fill="white", width=2, dash=(3, 2))

        self._signal_val_lbl.config(text=f"{self.current_signal:.1f}%")

    def _refresh_broadcast_panel(self):
        if self.is_broadcasting and self.broadcast_start:
            elapsed = int(time.time() - self.broadcast_start)
            h = elapsed // 3600
            m = (elapsed % 3600) // 60
            s = elapsed % 60
            self._timer_lbl.config(text=f"{h:02d}:{m:02d}:{s:02d}")

        if self.is_broadcasting and self._auto_stop and self.silence_start is not None:
            remaining = max(0, self._silence_sec - (time.time() - self.silence_start))
            self._silence_lbl.config(text=f"No signal - stopping in {remaining:.0f}s")
        elif self.is_broadcasting:
            self._silence_lbl.config(text="")

    def _log(self, msg):
        self._log_box.config(state=tk.NORMAL)
        ts = time.strftime("%H:%M:%S")
        self._log_box.insert(tk.END, f"[{ts}] {msg}\n")
        self._log_box.see(tk.END)
        self._log_box.config(state=tk.DISABLED)


if __name__ == "__main__":
    root = tk.Tk()
    BlastRadioController(root)
    root.mainloop()