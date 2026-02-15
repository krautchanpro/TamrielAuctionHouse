"""
=============================================================================
 Tamriel Auction House — Desktop Client (GUI)
 User-friendly GUI wrapper around the sync engine.
 Builds to standalone .exe / .app / binary via PyInstaller.
=============================================================================
"""

import json
import os
import platform
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from pathlib import Path

# Import the sync engine from client.py
from client import SyncEngine, APIClient, SavedVarsManager, detect_eso_dir, load_config, save_config

APP_NAME = "Tamriel Auction House"
CONFIG_FILE = "tah_config.json"
VERSION = "1.0.0"


class TAHClientGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title(f"{APP_NAME} — Desktop Client v{VERSION}")
        self.root.geometry("560x480")
        self.root.resizable(False, False)

        # State
        self.engine = None
        self.sync_thread = None
        self.running = False
        self.config = self._load_config()

        self._build_ui()
        self._populate_fields()

    def _load_config(self):
        config_path = self._config_path()
        if os.path.exists(config_path):
            with open(config_path) as f:
                return json.load(f)
        return {
            "server_url": "https://tamriel-ah.org",
            "eso_dir": detect_eso_dir() or "",
            "account_name": "",
            "api_key": "",
            "sync_interval": 5,
            "addon_name": "AuctionHouse",
            "saved_vars_name": "AuctionHouseVars",
        }

    def _config_path(self):
        if platform.system() == "Windows":
            appdata = os.environ.get("APPDATA", Path.home())
            d = Path(appdata) / "TamrielAuctionHouse"
        elif platform.system() == "Darwin":
            d = Path.home() / "Library" / "Application Support" / "TamrielAuctionHouse"
        else:
            d = Path.home() / ".config" / "tamriel-auction-house"
        d.mkdir(parents=True, exist_ok=True)
        return str(d / CONFIG_FILE)

    def _save_config(self):
        with open(self._config_path(), "w") as f:
            json.dump(self.config, f, indent=2)

    # -----------------------------------------------------------------------
    #  UI
    # -----------------------------------------------------------------------

    def _build_ui(self):
        # Header
        header = tk.Frame(self.root, bg="#1a1a2e", height=50)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        tk.Label(header, text=f"⚔  {APP_NAME}", font=("Arial", 16, "bold"),
                 fg="#FFD700", bg="#1a1a2e").pack(pady=10)

        # Settings frame
        settings = ttk.LabelFrame(self.root, text="Settings", padding=10)
        settings.pack(fill=tk.X, padx=15, pady=(10, 5))

        # Server URL
        ttk.Label(settings, text="Server URL:").grid(row=0, column=0, sticky="w", pady=3)
        self.server_var = tk.StringVar()
        ttk.Entry(settings, textvariable=self.server_var, width=45).grid(row=0, column=1, pady=3, padx=(5, 0))

        # ESO Directory
        ttk.Label(settings, text="ESO Directory:").grid(row=1, column=0, sticky="w", pady=3)
        eso_frame = tk.Frame(settings)
        eso_frame.grid(row=1, column=1, sticky="w", pady=3, padx=(5, 0))
        self.eso_var = tk.StringVar()
        ttk.Entry(eso_frame, textvariable=self.eso_var, width=35).pack(side=tk.LEFT)
        ttk.Button(eso_frame, text="Browse", command=self._browse_eso, width=8).pack(side=tk.LEFT, padx=(4, 0))

        # Player name
        ttk.Label(settings, text="Player Name:").grid(row=2, column=0, sticky="w", pady=3)
        self.player_var = tk.StringVar()
        ttk.Entry(settings, textvariable=self.player_var, width=45).grid(row=2, column=1, pady=3, padx=(5, 0))

        # API Key (read-only display)
        ttk.Label(settings, text="API Key:").grid(row=3, column=0, sticky="w", pady=3)
        self.apikey_var = tk.StringVar()
        e = ttk.Entry(settings, textvariable=self.apikey_var, width=45, state="readonly")
        e.grid(row=3, column=1, pady=3, padx=(5, 0))

        # Buttons
        btn_frame = tk.Frame(self.root)
        btn_frame.pack(fill=tk.X, padx=15, pady=8)

        self.start_btn = ttk.Button(btn_frame, text="▶  Start Syncing", command=self._start_sync)
        self.start_btn.pack(side=tk.LEFT, padx=(0, 5))

        self.stop_btn = ttk.Button(btn_frame, text="■  Stop", command=self._stop_sync, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=(0, 5))

        ttk.Button(btn_frame, text="Test Connection", command=self._test_connection).pack(side=tk.LEFT)

        # Status
        status_frame = ttk.LabelFrame(self.root, text="Status", padding=10)
        status_frame.pack(fill=tk.X, padx=15, pady=5)

        self.status_var = tk.StringVar(value="Not connected")
        tk.Label(status_frame, textvariable=self.status_var, font=("Arial", 11),
                 fg="#888888").pack(anchor="w")

        # Log
        log_frame = ttk.LabelFrame(self.root, text="Log", padding=5)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=15, pady=(5, 15))

        self.log_text = tk.Text(log_frame, height=8, font=("Consolas", 9),
                                bg="#1a1a1a", fg="#cccccc", state=tk.DISABLED, wrap=tk.WORD)
        scrollbar = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    def _populate_fields(self):
        self.server_var.set(self.config.get("server_url", ""))
        self.eso_var.set(self.config.get("eso_dir", ""))
        self.player_var.set(self.config.get("account_name", ""))
        key = self.config.get("api_key", "")
        self.apikey_var.set(key[:20] + "..." if len(key) > 20 else key)

    def _browse_eso(self):
        d = filedialog.askdirectory(title="Select ESO Documents Directory")
        if d:
            self.eso_var.set(d)

    # -----------------------------------------------------------------------
    #  Actions
    # -----------------------------------------------------------------------

    def _log(self, msg):
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, f"{time.strftime('%H:%M:%S')}  {msg}\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _set_status(self, text, color="#888888"):
        self.status_var.set(text)

    def _test_connection(self):
        url = self.server_var.get().strip()
        if not url:
            messagebox.showwarning("Missing", "Enter a server URL first.")
            return
        self._log(f"Testing connection to {url}...")
        try:
            api = APIClient(url)
            result = api.health()
            if result.get("status") == "healthy":
                self._log("✓ Server is healthy and database is connected!")
                self._set_status("Server reachable", "#00cc00")
            else:
                self._log(f"✗ Unexpected response: {result}")
                self._set_status("Unexpected response", "#cc0000")
        except Exception as e:
            self._log(f"✗ Connection failed: {e}")
            self._set_status("Connection failed", "#cc0000")

    def _start_sync(self):
        # Save current settings
        self.config["server_url"] = self.server_var.get().strip()
        self.config["eso_dir"] = self.eso_var.get().strip()
        self.config["account_name"] = self.player_var.get().strip()

        if not self.config["server_url"]:
            messagebox.showwarning("Missing", "Enter a server URL.")
            return
        if not self.config["eso_dir"]:
            messagebox.showwarning("Missing", "Select your ESO directory.")
            return
        if not self.config["account_name"]:
            messagebox.showwarning("Missing", "Enter your player name (e.g. @YourName).")
            return

        self._save_config()
        self._log("Starting sync engine...")

        self.engine = SyncEngine(self.config)

        # Check server
        if not self.engine.check_server():
            self._log("✗ Cannot reach server. Check URL and try again.")
            self._set_status("Server unreachable", "#cc0000")
            return

        # Register
        try:
            self.engine.ensure_registered()
            self.config["api_key"] = self.engine.config.get("api_key", "")
            self._save_config()
            key = self.config["api_key"]
            self.apikey_var.set(key[:20] + "..." if len(key) > 20 else key)
            self._log(f"Registered as {self.config['account_name']}")
        except Exception as e:
            self._log(f"✗ Registration failed: {e}")
            return

        self.running = True
        self.start_btn.configure(state=tk.DISABLED)
        self.stop_btn.configure(state=tk.NORMAL)
        self._set_status("Syncing...", "#00cc00")

        self.sync_thread = threading.Thread(target=self._sync_loop, daemon=True)
        self.sync_thread.start()

    def _sync_loop(self):
        cycle = 0
        while self.running:
            try:
                cycle += 1

                if self.engine.sv.has_changed():
                    self.engine.push_outgoing()
                    self.root.after(0, self._log, "Pushed outgoing actions")

                self.engine.pull_incoming()

                s = self.engine.stats
                if cycle % 12 == 0:  # Every ~60s at 5s interval
                    self.root.after(0, self._log,
                        f"Synced — {s['listings_synced']} listings | "
                        f"Pushes: {s['pushes']} | Pulls: {s['pulls']}")
                    self.root.after(0, self._set_status,
                        f"Connected — {s['listings_synced']} active listings", "#00cc00")

            except Exception as e:
                self.root.after(0, self._log, f"Sync error: {e}")

            time.sleep(self.config.get("sync_interval", 5))

        self.root.after(0, self._log, "Sync stopped.")
        self.root.after(0, self._set_status, "Stopped", "#888888")

    def _stop_sync(self):
        self.running = False
        self.start_btn.configure(state=tk.NORMAL)
        self.stop_btn.configure(state=tk.DISABLED)
        self._log("Stopping sync...")

    def run(self):
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.mainloop()

    def _on_close(self):
        self.running = False
        self.root.destroy()


def main():
    app = TAHClientGUI()
    app.run()


if __name__ == "__main__":
    main()
