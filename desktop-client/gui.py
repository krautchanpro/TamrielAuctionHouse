"""
=============================================================================
 Tamriel Auction House — Desktop Client
 Run-and-done: auto-detects everything, connects, and syncs.
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

from client import SyncEngine, APIClient, SavedVarsManager, detect_eso_dir, load_config, save_config

APP_NAME = "Tamriel Auction House"
SERVER_URL = "https://tamriel-ah.org"
CONFIG_FILE = "tah_config.json"
VERSION = "1.0.0"


class TAHClientGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title(f"{APP_NAME} v{VERSION}")
        self.root.geometry("480x360")
        self.root.resizable(False, False)

        self.engine = None
        self.sync_thread = None
        self.running = False
        self.config = self._load_config()

        self._build_ui()

        # Auto-start after UI renders
        self.root.after(500, self._auto_start)

    def _config_path(self):
        if platform.system() == "Windows":
            appdata = os.environ.get("APPDATA", str(Path.home()))
            d = Path(appdata) / "TamrielAuctionHouse"
        elif platform.system() == "Darwin":
            d = Path.home() / "Library" / "Application Support" / "TamrielAuctionHouse"
        else:
            d = Path.home() / ".config" / "tamriel-auction-house"
        d.mkdir(parents=True, exist_ok=True)
        return str(d / CONFIG_FILE)

    def _load_config(self):
        config_path = self._config_path()
        if os.path.exists(config_path):
            with open(config_path) as f:
                config = json.load(f)
            config["server_url"] = SERVER_URL
            return config
        return {
            "server_url": SERVER_URL,
            "eso_dir": detect_eso_dir() or "",
            "account_name": "",
            "api_key": "",
            "sync_interval": 5,
            "addon_name": "AuctionHouse",
            "saved_vars_name": "AuctionHouseVars",
        }

    def _save_config(self):
        with open(self._config_path(), "w") as f:
            json.dump(self.config, f, indent=2)

    # -----------------------------------------------------------------------
    #  UI — minimal, just status + log
    # -----------------------------------------------------------------------

    def _build_ui(self):
        # Header
        header = tk.Frame(self.root, bg="#1a1a2e", height=50)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        tk.Label(header, text=f"⚔  {APP_NAME}", font=("Arial", 14, "bold"),
                 fg="#FFD700", bg="#1a1a2e").pack(pady=10)

        # Status area
        status_frame = ttk.Frame(self.root, padding=10)
        status_frame.pack(fill=tk.X, padx=15, pady=(10, 0))

        self.status_icon = tk.Label(status_frame, text="●", font=("Arial", 16), fg="#888888")
        self.status_icon.pack(side=tk.LEFT)

        self.status_var = tk.StringVar(value="Starting...")
        tk.Label(status_frame, textvariable=self.status_var, font=("Arial", 12)).pack(side=tk.LEFT, padx=(8, 0))

        # Info labels
        info_frame = ttk.Frame(self.root, padding=(15, 5))
        info_frame.pack(fill=tk.X)

        self.player_var = tk.StringVar(value="Player: detecting...")
        tk.Label(info_frame, textvariable=self.player_var, font=("Arial", 9), fg="#666666").pack(anchor="w")

        self.listings_var = tk.StringVar(value="Listings: —")
        tk.Label(info_frame, textvariable=self.listings_var, font=("Arial", 9), fg="#666666").pack(anchor="w")

        self.syncs_var = tk.StringVar(value="Syncs: 0")
        tk.Label(info_frame, textvariable=self.syncs_var, font=("Arial", 9), fg="#666666").pack(anchor="w")

        # Log
        log_frame = ttk.LabelFrame(self.root, text="Log", padding=5)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=15, pady=(5, 10))

        self.log_text = tk.Text(log_frame, height=8, font=("Consolas", 9),
                                bg="#1a1a1a", fg="#cccccc", state=tk.DISABLED, wrap=tk.WORD)
        scrollbar = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Bottom bar with minimize to tray hint
        bottom = ttk.Frame(self.root, padding=(15, 0, 15, 8))
        bottom.pack(fill=tk.X)
        tk.Label(bottom, text="Minimize this window — it will keep syncing in the background.",
                 font=("Arial", 8), fg="#999999").pack(anchor="w")

    def _log(self, msg):
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, f"{time.strftime('%H:%M:%S')}  {msg}\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _set_status(self, text, color="#888888"):
        self.status_var.set(text)
        self.status_icon.configure(fg=color)

    # -----------------------------------------------------------------------
    #  Auto-start: detect everything and begin syncing
    # -----------------------------------------------------------------------

    def _auto_start(self):
        self._log("Detecting ESO installation...")

        # 1. Detect ESO directory
        if not self.config.get("eso_dir"):
            self.config["eso_dir"] = detect_eso_dir() or ""

        if not self.config["eso_dir"]:
            self._log("Could not find ESO directory automatically.")
            self._set_status("ESO not found", "#cc0000")
            self._ask_eso_dir()
            return

        self._log(f"ESO directory: {self.config['eso_dir']}")

        # 2. Detect player name from SavedVariables
        if not self.config.get("account_name"):
            self._log("Detecting player name...")
            self.config["account_name"] = self._detect_player_name()

        if not self.config["account_name"]:
            self._log("Could not detect player name. Log into ESO first, then restart.")
            self._set_status("Log into ESO first", "#cc8800")
            # Retry in 30 seconds
            self.root.after(30000, self._auto_start)
            return

        self._log(f"Player: {self.config['account_name']}")
        self.player_var.set(f"Player: {self.config['account_name']}")

        # 3. Connect to server
        self._log(f"Connecting to {SERVER_URL}...")
        self.engine = SyncEngine(self.config)

        if not self.engine.check_server():
            self._log("Server unreachable. Retrying in 30 seconds...")
            self._set_status("Server offline — retrying", "#cc8800")
            self.root.after(30000, self._auto_start)
            return

        self._log("Server connected!")

        # 4. Register / authenticate
        try:
            self.engine.ensure_registered()
            self.config["api_key"] = self.engine.config.get("api_key", "")
            self._save_config()
            self._log("Authenticated!")
        except Exception as e:
            self._log(f"Registration failed: {e}. Retrying in 30s...")
            self._set_status("Auth failed — retrying", "#cc0000")
            self.root.after(30000, self._auto_start)
            return

        # 5. Start sync loop
        self.running = True
        self._set_status("Connected — syncing", "#00cc00")
        self._log("Syncing started. You can minimize this window.")

        self.sync_thread = threading.Thread(target=self._sync_loop, daemon=True)
        self.sync_thread.start()

    def _detect_player_name(self):
        """Scan SavedVariables files to find the account name."""
        sv_dir = Path(self.config["eso_dir"]) / "SavedVariables"
        if not sv_dir.exists():
            return ""

        # Look through any .lua file for @PlayerName patterns
        import re
        for lua_file in sv_dir.glob("*.lua"):
            try:
                content = lua_file.read_text(encoding="utf-8", errors="replace")
                # ESO SavedVariables use ["@AccountName"] as keys
                matches = re.findall(r'\["(@[^"]+)"\]', content)
                for match in matches:
                    if match.startswith("@") and len(match) > 2:
                        return match
            except Exception:
                continue
        return ""

    def _ask_eso_dir(self):
        """Fallback: ask user to pick ESO directory."""
        self._log("Please select your ESO documents folder.")
        d = filedialog.askdirectory(title="Select Elder Scrolls Online folder (e.g. .../Elder Scrolls Online/live)")
        if d:
            sv_check = Path(d) / "SavedVariables"
            if sv_check.exists():
                self.config["eso_dir"] = d
                self._save_config()
                self._log(f"ESO directory set: {d}")
                self._auto_start()
            else:
                self._log("That folder doesn't contain SavedVariables. Try again.")
                self._set_status("Invalid ESO directory", "#cc0000")
        else:
            self._log("No folder selected. Retry in 30 seconds or restart the app.")
            self.root.after(30000, self._auto_start)

    # -----------------------------------------------------------------------
    #  Sync loop
    # -----------------------------------------------------------------------

    def _sync_loop(self):
        cycle = 0
        while self.running:
            try:
                cycle += 1

                if self.engine.sv.has_changed():
                    self.engine.push_outgoing()
                    self.root.after(0, self._log, "Pushed new data to server")

                self.engine.pull_incoming()

                s = self.engine.stats
                self.root.after(0, self.listings_var.set,
                    f"Listings: {s['listings_synced']} active")
                self.root.after(0, self.syncs_var.set,
                    f"Syncs: {s['pulls']} pulls / {s['pushes']} pushes")

                if cycle % 60 == 0:
                    self.root.after(0, self._log,
                        f"Running — {s['listings_synced']} listings, "
                        f"{s['pulls']} pulls, {s['pushes']} pushes")

            except Exception as e:
                self.root.after(0, self._log, f"Sync error: {e}")
                self.root.after(0, self._set_status, "Sync error — retrying", "#cc8800")
                time.sleep(10)
                self.root.after(0, self._set_status, "Connected — syncing", "#00cc00")
                continue

            time.sleep(self.config.get("sync_interval", 5))

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
