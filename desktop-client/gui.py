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
        self.root.geometry("550x480")
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
            "saved_vars_name": "AuctionHouse",
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

        # Tabs
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=15, pady=(5, 0))

        # Log tab
        log_frame = ttk.Frame(self.notebook, padding=5)
        self.notebook.add(log_frame, text="Log")

        self.log_text = tk.Text(log_frame, height=8, font=("Consolas", 9),
                                bg="#1a1a1a", fg="#cccccc", state=tk.DISABLED, wrap=tk.WORD)
        scrollbar = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Sales History tab
        sales_frame = ttk.Frame(self.notebook, padding=5)
        self.notebook.add(sales_frame, text="Sales History")

        # Refresh button
        btn_frame = ttk.Frame(sales_frame)
        btn_frame.pack(fill=tk.X, pady=(0, 5))
        ttk.Button(btn_frame, text="Refresh", command=self._refresh_sales).pack(side=tk.RIGHT)
        self.sales_total_var = tk.StringVar(value="")
        tk.Label(btn_frame, textvariable=self.sales_total_var, font=("Arial", 9),
                 fg="#666666").pack(side=tk.LEFT)

        # Sales table
        cols = ("item", "qty", "price", "buyer", "state", "sold")
        self.sales_tree = ttk.Treeview(sales_frame, columns=cols, show="headings", height=8)
        self.sales_tree.heading("item", text="Item")
        self.sales_tree.heading("qty", text="Qty")
        self.sales_tree.heading("price", text="Price")
        self.sales_tree.heading("buyer", text="Buyer")
        self.sales_tree.heading("state", text="Status")
        self.sales_tree.heading("sold", text="Sold")

        self.sales_tree.column("item", width=160)
        self.sales_tree.column("qty", width=35, anchor="center")
        self.sales_tree.column("price", width=70, anchor="e")
        self.sales_tree.column("buyer", width=100)
        self.sales_tree.column("state", width=80, anchor="center")
        self.sales_tree.column("sold", width=100)

        sales_scroll = ttk.Scrollbar(sales_frame, orient=tk.VERTICAL, command=self.sales_tree.yview)
        self.sales_tree.configure(yscrollcommand=sales_scroll.set)
        self.sales_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sales_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # Bottom bar
        bottom = ttk.Frame(self.root, padding=(15, 0, 15, 8))
        bottom.pack(fill=tk.X)
        tk.Label(bottom, text="Minimize this window — it will keep syncing in the background.",
                 font=("Arial", 8), fg="#999999").pack(anchor="w")

    def _refresh_sales(self):
        """Fetch and display sales history from server."""
        if not self.engine or not self.engine.player_name:
            return
        threading.Thread(target=self._fetch_sales, daemon=True).start()

    def _fetch_sales(self):
        try:
            sales = self.engine.api.get_sales_history(self.engine.player_name)
            self.root.after(0, self._populate_sales, sales)
        except Exception as e:
            self.root.after(0, self._log, f"Failed to load sales: {e}")

    def _populate_sales(self, sales):
        # Clear existing
        for item in self.sales_tree.get_children():
            self.sales_tree.delete(item)

        total_gold = 0
        for sale in sales:
            state_map = {
                "awaiting_cod": "Awaiting COD",
                "cod_sent": "COD Sent",
                "completed": "Completed",
            }
            state_display = state_map.get(sale.get("state", ""), sale.get("state", ""))

            sold_at = sale.get("sold_at", "")
            if sold_at:
                try:
                    from datetime import datetime as dt
                    t = dt.fromisoformat(sold_at.replace("Z", "+00:00"))
                    sold_at = t.strftime("%b %d %H:%M")
                except Exception:
                    pass

            self.sales_tree.insert("", tk.END, values=(
                sale.get("item_name", "?"),
                sale.get("quantity", 1),
                f"{sale.get('price', 0):,}g",
                sale.get("buyer", "?"),
                state_display,
                sold_at,
            ))
            total_gold += sale.get("price", 0)

        self.sales_total_var.set(f"{len(sales)} sales — {total_gold:,}g total")

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
        initial_done = False
        while self.running:
            try:
                cycle += 1

                # Initial sync on startup
                if not initial_done:
                    self.root.after(0, self._log, "Initial sync...")
                    self.engine.push_outgoing()
                    self.engine.pull_incoming()
                    initial_done = True
                    self.root.after(0, self._log, "Ready! Use /ah refresh in-game to sync.")

                # Sync only when addon requests it (/reloadui or /ah refresh)
                elif self.engine.sv.has_changed() and self.engine._check_sync_request():
                    self.root.after(0, self._log, "Sync requested — pushing and pulling...")
                    self.engine.push_outgoing()
                    self.engine.pull_incoming()
                    self.root.after(0, self._log, "Sync complete!")

                # Heartbeat every ~30 seconds
                if cycle % 6 == 0:
                    try:
                        self.engine.api.session.get(
                            f"{self.engine.api.server_url}/api/v1/stats",
                            timeout=5
                        )
                    except Exception:
                        pass

                    # Check for purchase notifications
                    self.engine.check_notifications()
                    if hasattr(self.engine, '_last_notif_count') and self.engine._last_notif_count > 0:
                        self.root.after(0, self._log,
                            f"Processed {self.engine._last_notif_count} notification(s)")
                    # Show sale popups for any sold items
                    if hasattr(self.engine, '_sale_alerts'):
                        for sale in self.engine._sale_alerts:
                            self.root.after(0, self._show_sale_popup,
                                sale.get("item_name", "Unknown"),
                                sale.get("buyer", "Unknown"),
                                sale.get("price", 0),
                                sale.get("quantity", 1))
                        self.engine._sale_alerts = []

                s = self.engine.stats
                self.root.after(0, self.listings_var.set,
                    f"Listings: {s['listings_synced']} active")
                self.root.after(0, self.syncs_var.set,
                    f"Syncs: {s['pulls']} pulls / {s['pushes']} pushes")

                if cycle % 120 == 0:
                    self.root.after(0, self._log,
                        f"Idle — {s['listings_synced']} listings, "
                        f"{s['pulls']} pulls, {s['pushes']} pushes")

            except Exception as e:
                self.root.after(0, self._log, f"Sync error: {e}")
                self.root.after(0, self._set_status, "Sync error — retrying", "#cc8800")
                time.sleep(10)
                self.root.after(0, self._set_status, "Connected — syncing", "#00cc00")
                continue

            time.sleep(5)

    def run(self):
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.mainloop()

    def _on_close(self):
        self.running = False
        self.root.destroy()

    def _show_sale_popup(self, item_name, buyer, price, quantity=1):
        """Show a popup window when an item sells, with sound."""

        # Play sound
        try:
            if platform.system() == "Windows":
                import winsound
                winsound.PlaySound("SystemExclamation", winsound.SND_ALIAS | winsound.SND_ASYNC)
            elif platform.system() == "Darwin":
                import subprocess
                subprocess.Popen(["afplay", "/System/Library/Sounds/Glass.aiff"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                import subprocess
                # Try various Linux sound options
                for cmd in [
                    ["paplay", "/usr/share/sounds/freedesktop/stereo/message.oga"],
                    ["aplay", "/usr/share/sounds/freedesktop/stereo/message.oga"],
                    ["canberra-gtk-play", "-i", "message"],
                ]:
                    try:
                        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        break
                    except FileNotFoundError:
                        continue
        except Exception:
            pass

        # Create popup window
        popup = tk.Toplevel(self.root)
        popup.title("Item Sold!")
        popup.geometry("380x220")
        popup.resizable(False, False)
        popup.attributes("-topmost", True)
        popup.configure(bg="#1a1a2e")

        # Try to center on screen
        popup.update_idletasks()
        sw = popup.winfo_screenwidth()
        sh = popup.winfo_screenheight()
        x = (sw - 380) // 2
        y = (sh - 220) // 2
        popup.geometry(f"380x220+{x}+{y}")

        # Gold header
        header = tk.Frame(popup, bg="#2d1f0e", height=45)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        tk.Label(header, text="★  ITEM SOLD!  ★", font=("Arial", 16, "bold"),
                 fg="#FFD700", bg="#2d1f0e").pack(pady=8)

        # Content
        content = tk.Frame(popup, bg="#1a1a2e", padx=20, pady=15)
        content.pack(fill=tk.BOTH, expand=True)

        tk.Label(content, text=item_name, font=("Arial", 14, "bold"),
                 fg="#FFFFFF", bg="#1a1a2e").pack(anchor="w")

        if quantity > 1:
            tk.Label(content, text=f"Quantity: {quantity}",
                     font=("Arial", 11), fg="#AAAAAA", bg="#1a1a2e").pack(anchor="w", pady=(2, 0))

        tk.Label(content, text=f"Price: {price:,} gold",
                 font=("Arial", 12), fg="#FFD700", bg="#1a1a2e").pack(anchor="w", pady=(5, 0))

        tk.Label(content, text=f"Buyer: {buyer}",
                 font=("Arial", 11), fg="#AAAAAA", bg="#1a1a2e").pack(anchor="w", pady=(2, 0))

        tk.Label(content, text="Send them a COD to complete the sale!",
                 font=("Arial", 10, "italic"), fg="#66BB6A", bg="#1a1a2e").pack(anchor="w", pady=(10, 0))

        # Dismiss button
        btn_frame = tk.Frame(popup, bg="#1a1a2e", pady=8)
        btn_frame.pack(fill=tk.X)
        tk.Button(btn_frame, text="OK", font=("Arial", 11, "bold"),
                  bg="#FFD700", fg="#1a1a2e", activebackground="#FFC107",
                  width=12, command=popup.destroy).pack()

        # Auto-close after 30 seconds
        popup.after(30000, lambda: popup.destroy() if popup.winfo_exists() else None)

        # Focus the popup
        popup.focus_force()
        popup.lift()


def main():
    app = TAHClientGUI()
    app.run()


if __name__ == "__main__":
    main()
