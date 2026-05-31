import threading
import time
import tkinter as tk
from tkinter import ttk
import json
import os

import numpy as np
import pyautogui
import pygetwindow as gw
import sounddevice as sd

pyautogui.FAILSAFE = False

class BlastRadioController:

    BG       = "#0f0f1a"
    PANEL    = "#16213e"
    ACCENT   = "#e94560"
    GREEN    = "#4ecca3"
    YELLOW   = "#f5a623"
    TEXT     = "#e0e0e0"
    DIM      = "#667788"
    ENTRY_BG = "#0d1b2a"

    APP_TITLE = "Blast Radio"
    CONFIG_FILE = "blast_radio_config.json"

    def __init__(self, root):
        self.root = root
        self.root.title("Blast Radio Controller")
        self.root.minsize(520, 700)
        self.root.resizable(True, True)
        self.root.configure(bg=self.BG)

        # Core state
        self.is_broadcasting  = False
        self.broadcast_locked = False
        self.silence_start    = None
        self.broadcast_start  = None
        self.current_volume   = 0.0
        
        self.window_visible   = False
        self.start_coords     = None  # These are now RELATIVE to the window
        self.stop_coords      = None  # These are now RELATIVE to the window

        # Load saved coordinates
        self._load_config()

        # Plain attributes read by the audio thread
        self._threshold   = 2.0
        self._silence_sec = 30
        self._auto_start  = True
        self._auto_stop   = True

        self._input_devices  = []
        self._stream_restart = threading.Event()

        # Tkinter variables
        self.device_var      = tk.StringVar()
        self.threshold_var   = tk.DoubleVar(value=2.0)
        self.silence_sec_var = tk.IntVar(value=30)
        self.auto_start_var  = tk.BooleanVar(value=True)
        self.auto_stop_var   = tk.BooleanVar(value=True)

        self.threshold_var.trace_add("write", lambda *_: setattr(self, "_threshold", self.threshold_var.get()))
        self.silence_sec_var.trace_add("write", lambda *_: setattr(self, "_silence_sec", self.silence_sec_var.get()))
        self.auto_start_var.trace_add("write", lambda *_: setattr(self, "_auto_start", self.auto_start_var.get()))
        self.auto_stop_var.trace_add("write", lambda *_: setattr(self, "_auto_stop", self.auto_stop_var.get()))

        self._setup_styles()
        self._build_ui()
        self._populate_devices()

        threading.Thread(target=self._audio_thread, daemon=True).start()
        threading.Thread(target=self._window_watcher, daemon=True).start()
        self._ui_loop()

    def _load_config(self):
        if os.path.exists(self.CONFIG_FILE):
            try:
                with open(self.CONFIG_FILE, "r") as f:
                    data = json.load(f)
                    if data.get("start_coords"):
                        self.start_coords = tuple(data["start_coords"])
                    if data.get("stop_coords"):
                        self.stop_coords = tuple(data["stop_coords"])
            except Exception as e:
                print(f"Error loading config: {e}")

    def _save_config(self):
        data = {
            "start_coords": self.start_coords,
            "stop_coords": self.stop_coords
        }
        try:
            with open(self.CONFIG_FILE, "w") as f:
                json.dump(data, f)
        except Exception as e:
            print(f"Error saving config: {e}")

    def _setup_styles(self):
        s = ttk.Style()
        s.theme_use("clam")
        s.configure("TCombobox", fieldbackground=self.ENTRY_BG, background=self.ENTRY_BG, foreground=self.TEXT, selectbackground=self.ACCENT)
        s.configure("TSpinbox", fieldbackground=self.ENTRY_BG, foreground=self.TEXT, background=self.ENTRY_BG)
        s.configure("TCheckbutton", background=self.PANEL, foreground=self.TEXT, focuscolor=self.PANEL)

    def _build_ui(self):
        hdr = tk.Frame(self.root, bg=self.ACCENT, height=54)
        hdr.pack(fill=tk.X)
        hdr.pack_propagate(False)
        tk.Label(hdr, text="BLAST RADIO CONTROLLER", font=("Helvetica", 13, "bold"), bg=self.ACCENT, fg="white").pack(expand=True)

        wrap = tk.Frame(self.root, bg=self.BG, padx=16, pady=12)
        wrap.pack(fill=tk.BOTH, expand=True)

        self._panel_setup(wrap)
        self._panel_audio(wrap)
        self._panel_automation(wrap)
        self._panel_controls(wrap)
        self._panel_log(wrap)

    def _section(self, parent, title):
        tk.Label(parent, text=title, font=("Helvetica", 8, "bold"), bg=self.BG, fg=self.DIM).pack(anchor=tk.W, pady=(0, 3))
        inner = tk.Frame(parent, bg=self.PANEL, padx=14, pady=10)
        inner.pack(fill=tk.X, pady=(0, 10))
        return inner

    # 1. SETUP PANEL
    def _panel_setup(self, p):
        f = self._section(p, "1. WINDOW & BUTTON SETUP")

        self.win_status_lbl = tk.Label(f, text="Checking for Blast Radio window...", bg=self.PANEL, fg=self.YELLOW, font=("Helvetica", 10, "bold"))
        self.win_status_lbl.pack(anchor=tk.W, pady=(0, 10))

        btn_frame = tk.Frame(f, bg=self.PANEL)
        btn_frame.pack(fill=tk.X)

        start_text = f"Start Button: Rel X={self.start_coords[0]}, Y={self.start_coords[1]}" if self.start_coords else "Start Button: Not Set"
        start_color = self.GREEN if self.start_coords else self.DIM
        self.start_btn_lbl = tk.Label(btn_frame, text=start_text, bg=self.PANEL, fg=start_color)
        self.start_btn_lbl.grid(row=0, column=0, sticky=tk.W, pady=5)
        tk.Button(btn_frame, text="Pick Start", bg=self.ENTRY_BG, fg=self.TEXT, relief=tk.FLAT, command=lambda: self._pick_position("start")).grid(row=0, column=1, padx=10)

        stop_text = f"Stop Button: Rel X={self.stop_coords[0]}, Y={self.stop_coords[1]}" if self.stop_coords else "Stop Button: Not Set"
        stop_color = self.GREEN if self.stop_coords else self.DIM
        self.stop_btn_lbl = tk.Label(btn_frame, text=stop_text, bg=self.PANEL, fg=stop_color)
        self.stop_btn_lbl.grid(row=1, column=0, sticky=tk.W, pady=5)
        tk.Button(btn_frame, text="Pick Stop", bg=self.ENTRY_BG, fg=self.TEXT, relief=tk.FLAT, command=lambda: self._pick_position("stop")).grid(row=1, column=1, padx=10)

    # 2. AUDIO PANEL
    def _panel_audio(self, p):
        f = self._section(p, "2. AUDIO SOURCE & METER")
        
        self._device_dd = ttk.Combobox(f, textvariable=self.device_var, state="readonly")
        self._device_dd.pack(fill=tk.X, pady=(0, 10))
        self._device_dd.bind("<<ComboboxSelected>>", lambda _: self._stream_restart.set())

        self._vol_canvas = tk.Canvas(f, height=22, bg=self.PANEL, highlightthickness=0)
        self._vol_canvas.pack(fill=tk.X, pady=(0, 10))

        row = tk.Frame(f, bg=self.PANEL)
        row.pack(fill=tk.X)
        tk.Label(row, text="Trigger Threshold", bg=self.PANEL, fg=self.TEXT).pack(side=tk.LEFT)
        self._thresh_lbl = tk.Label(row, text="2.0", width=5, bg=self.PANEL, fg=self.ACCENT, font=("Helvetica", 10, "bold"))
        self._thresh_lbl.pack(side=tk.RIGHT)

        tk.Scale(f, from_=0.1, to=10.0, resolution=0.1, orient=tk.HORIZONTAL, variable=self.threshold_var, bg=self.PANEL, fg=self.TEXT, highlightthickness=0, troughcolor=self.ENTRY_BG, activebackground=self.ACCENT, showvalue=False, command=lambda v: self._thresh_lbl.config(text=f"{float(v):.1f}")).pack(fill=tk.X)

    # 3. AUTOMATION PANEL
    def _panel_automation(self, p):
        f = self._section(p, "3. AUTOMATION SETTINGS")

        ttk.Checkbutton(f, text="Auto-start broadcast when audio crosses threshold", variable=self.auto_start_var).pack(anchor=tk.W, pady=(0, 5))
        
        ttk.Checkbutton(f, text="Auto-stop broadcast after silence", variable=self.auto_stop_var).pack(anchor=tk.W, pady=(0, 5))

        row = tk.Frame(f, bg=self.PANEL)
        row.pack(fill=tk.X, pady=(5, 0))
        tk.Label(row, text="Silence duration before stopping (seconds):", bg=self.PANEL, fg=self.TEXT).pack(side=tk.LEFT)
        ttk.Spinbox(row, from_=5, to=600, increment=5, textvariable=self.silence_sec_var, width=7).pack(side=tk.RIGHT)

    # 4. CONTROLS PANEL
    def _panel_controls(self, p):
        f = self._section(p, "4. CONTROLS & STATUS")

        top = tk.Frame(f, bg=self.PANEL)
        top.pack(fill=tk.X, pady=(0, 10))

        self._status_lbl = tk.Label(top, text="Idle", bg=self.PANEL, fg=self.DIM, font=("Helvetica", 11, "bold"))
        self._status_lbl.pack(side=tk.LEFT)

        self._timer_lbl = tk.Label(top, text="", bg=self.PANEL, fg=self.ACCENT, font=("Helvetica", 11, "bold"))
        self._timer_lbl.pack(side=tk.LEFT, padx=(12, 0))

        self._silence_lbl = tk.Label(f, text="", bg=self.PANEL, fg=self.YELLOW, font=("Helvetica", 9))
        self._silence_lbl.pack(anchor=tk.W, pady=(0, 8))

        btn_row = tk.Frame(f, bg=self.PANEL)
        btn_row.pack(fill=tk.X)

        tk.Button(btn_row, text="START BROADCAST", font=("Helvetica", 10, "bold"), bg=self.GREEN, fg="#0f0f1a", relief=tk.FLAT, padx=18, pady=9, cursor="hand2", command=self._manual_start).pack(side=tk.LEFT, padx=(0, 10))
        tk.Button(btn_row, text="STOP BROADCAST", font=("Helvetica", 10, "bold"), bg=self.ACCENT, fg="white", relief=tk.FLAT, padx=18, pady=9, cursor="hand2", command=self._manual_stop).pack(side=tk.LEFT)

    # 5. LOG PANEL
    def _panel_log(self, p):
        f = self._section(p, "ACTIVITY LOG")
        wrap = tk.Frame(f, bg=self.PANEL)
        wrap.pack(fill=tk.BOTH, expand=True)

        self._log_box = tk.Text(wrap, height=6, bg="#090d15", fg=self.GREEN, font=("Courier", 9), state=tk.DISABLED, wrap=tk.WORD, relief=tk.FLAT, padx=8, pady=6)
        self._log_box.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        sb = tk.Scrollbar(wrap, command=self._log_box.yview, bg=self.PANEL, troughcolor=self.ENTRY_BG, relief=tk.FLAT)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self._log_box.configure(yscrollcommand=sb.set)

    # LOGIC
    def _populate_devices(self):
        try:
            devs = sd.query_devices()
            self._input_devices = [(i, d["name"]) for i, d in enumerate(devs) if d["max_input_channels"] > 0]
            names = [n for _, n in self._input_devices]
            self._device_dd["values"] = names
            if names:
                self._device_dd.current(0)
        except Exception as e:
            self._log(f"Device error: {e}")

    def _selected_device(self):
        idx = self._device_dd.current()
        if 0 <= idx < len(self._input_devices):
            return self._input_devices[idx][0]
        return None

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
            
            # Calculate relative coordinates
            rel_x = abs_x - win.left
            rel_y = abs_y - win.top
            
            if target == "start":
                self.start_coords = (rel_x, rel_y)
                self.root.after(0, lambda: self.start_btn_lbl.config(text=f"Start Button: Rel X={rel_x}, Y={rel_y}", fg=self.GREEN))
            else:
                self.stop_coords = (rel_x, rel_y)
                self.root.after(0, lambda: self.stop_btn_lbl.config(text=f"Stop Button: Rel X={rel_x}, Y={rel_y}", fg=self.GREEN))
                
            self._save_config()
            self.root.after(0, self.root.deiconify)
            self.root.after(0, self._log, f"{target.capitalize()} button relative position saved.")

        threading.Thread(target=capture, daemon=True).start()

    def _get_blast_window(self):
        wins = [w for w in gw.getAllWindows() if w.title and self.APP_TITLE in w.title and "Controller" not in w.title]
        return wins[0] if wins else None

    def _window_watcher(self):
        while True:
            win = self._get_blast_window()
            if win and not win.isMinimized:
                self.window_visible = True
                self.root.after(0, lambda: self.win_status_lbl.config(text="Blast Radio window is visible and ready.", fg=self.GREEN))
            else:
                self.window_visible = False
                self.root.after(0, lambda: self.win_status_lbl.config(text="WARNING: Blast Radio window not found or minimized!", fg=self.ACCENT))
            time.sleep(1)

    def _safe_click(self, rel_coords):
        if not self.window_visible:
            self._log("WARNING: Click aborted. Blast Radio window is not visible.")
            return False
        
        win = self._get_blast_window()
        if win:
            try:
                win.activate()
                time.sleep(0.2)
            except:
                pass
            
            # Convert relative coordinates back to absolute screen coordinates
            abs_x = win.left + rel_coords[0]
            abs_y = win.top + rel_coords[1]
                
            try:
                pyautogui.click(abs_x, abs_y)
                return True
            except Exception as e:
                self._log(f"Click error: {e}")
                return False
        else:
            self._log("WARNING: Click aborted. Window lost.")
            return False

    def _audio_thread(self):
        while True:
            self._stream_restart.clear()
            dev = self._selected_device()
            try:
                with sd.InputStream(device=dev, callback=self._audio_cb, channels=1, samplerate=44100, blocksize=2048):
                    self.root.after(0, self._log, f"Listening on: {self.device_var.get() or str(dev)}")
                    self._stream_restart.wait()
            except Exception as e:
                self.root.after(0, self._log, f"Audio stream error: {e}")
                time.sleep(3)

    def _audio_cb(self, indata, _frames, _t, _status):
        vol = float(np.linalg.norm(indata) * 10)
        self.current_volume = vol

        if vol > self._threshold:
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

    def _do_start(self, mode="manual"):
        if self.is_broadcasting or self.broadcast_locked:
            return
        if not self.start_coords:
            self._log("Cannot start: Start button coordinates not set.")
            return
            
        self.broadcast_locked = True
        if self._safe_click(self.start_coords):
            self.is_broadcasting = True
            self.broadcast_start = time.time()
            self.silence_start = None
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
        if self._safe_click(self.stop_coords):
            self.is_broadcasting = False
            self.broadcast_start = None
            self.silence_start = None
            self._on_state_change(False)
            self._log(f"Broadcast stopped ({mode}).")
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

    def _ui_loop(self):
        self._draw_volume_bar()
        self._refresh_broadcast_panel()
        self.root.after(100, self._ui_loop)

    def _draw_volume_bar(self):
        c = self._vol_canvas
        c.update_idletasks()
        w = c.winfo_width()
        if w < 2: return
        c.delete("all")
        c.create_rectangle(0, 0, w, 22, fill=self.ENTRY_BG, outline="")

        pct = min(self.current_volume / 20.0, 1.0)
        bw  = int(pct * w)
        if bw > 0:
            color = self.GREEN if pct < 0.6 else self.YELLOW if pct < 0.85 else self.ACCENT
            c.create_rectangle(0, 3, bw, 19, fill=color, outline="")

        tp = min(self._threshold / 20.0, 1.0)
        tx = int(tp * w)
        c.create_line(tx, 0, tx, 22, fill="white", width=2, dash=(3, 2))

    def _refresh_broadcast_panel(self):
        if self.is_broadcasting and self.broadcast_start:
            elapsed = int(time.time() - self.broadcast_start)
            h, m, s = elapsed // 3600, (elapsed % 3600) // 60, elapsed % 60
            self._timer_lbl.config(text=f"{h:02d}:{m:02d}:{s:02d}")

        if self.is_broadcasting and self._auto_stop and self.silence_start is not None:
            remaining = max(0, self._silence_sec - (time.time() - self.silence_start))
            self._silence_lbl.config(text=f"Silence detected - stopping in {remaining:.0f}s")
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