"""
=============================================================================
 Tamriel Auction House — Desktop Client
 Bridges the ESO addon (SavedVariables) and the server (REST API).

 Features:
   - Watches SavedVariables file for outgoing actions (listings, purchases)
   - Pushes actions to the server
   - Pulls active listings from server and writes to SavedVariables
   - Polls for purchase notifications and writes to SavedVariables
   - Auto-registers player on first run
   - Configurable sync interval

 Usage:
   python client.py --config config.json
   python client.py --server http://your-server:8000 --eso-dir "C:/path/to/eso"
=============================================================================
"""

import argparse
import json
import hashlib
import logging
import os
import platform
import re
import sys
import time
from pathlib import Path
from typing import Optional

import requests

# ---------------------------------------------------------------------------
#  Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("TAH-Client")

# ---------------------------------------------------------------------------
#  Config
# ---------------------------------------------------------------------------

DEFAULT_CONFIG = {
    "server_url": "http://localhost:8000",
    "eso_dir": None,  # Auto-detected
    "account_name": None,  # Auto-detected from SavedVariables
    "api_key": None,  # Obtained on first registration
    "sync_interval": 5,  # seconds between sync cycles
    "addon_name": "AuctionHouse",
    "saved_vars_name": "AuctionHouseVars",
}


def detect_eso_dir() -> Optional[str]:
    """Auto-detect the ESO documents directory."""
    system = platform.system()
    home = Path.home()

    candidates = []
    if system == "Windows":
        candidates = [
            home / "Documents" / "Elder Scrolls Online" / "live",
            home / "Documents" / "Elder Scrolls Online" / "pts",
            Path(os.environ.get("USERPROFILE", "")) / "Documents" / "Elder Scrolls Online" / "live",
        ]
    elif system == "Darwin":
        candidates = [
            home / "Documents" / "Elder Scrolls Online" / "live",
        ]
    else:
        # Linux (e.g. via Steam/Proton) — multiple possible paths
        steam_roots = [
            home / ".steam" / "steam" / "steamapps",
            home / ".local" / "share" / "Steam" / "steamapps",
        ]
        doc_names = ["Documents", "My Documents"]
        for steam in steam_roots:
            for doc in doc_names:
                candidates.append(
                    steam / "compatdata" / "306130" / "pfx" /
                    "drive_c" / "users" / "steamuser" / doc / "Elder Scrolls Online" / "live"
                )

    for path in candidates:
        sv_dir = path / "SavedVariables"
        if sv_dir.exists():
            log.info("Detected ESO directory: %s", path)
            return str(path)

    return None


def load_config(config_path: Optional[str]) -> dict:
    """Load configuration from file or create defaults."""
    config = DEFAULT_CONFIG.copy()

    if config_path and os.path.exists(config_path):
        with open(config_path) as f:
            user_config = json.load(f)
        config.update(user_config)
        log.info("Loaded config from %s", config_path)

    if not config["eso_dir"]:
        config["eso_dir"] = detect_eso_dir()
        if not config["eso_dir"]:
            log.error("Could not detect ESO directory. Please specify --eso-dir")
            sys.exit(1)

    return config


def save_config(config: dict, config_path: str):
    """Save configuration to file."""
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)
    log.info("Saved config to %s", config_path)


# ---------------------------------------------------------------------------
#  SavedVariables Parser
# ---------------------------------------------------------------------------

class SavedVarsManager:
    """Read and write ESO SavedVariables Lua files."""

    def __init__(self, eso_dir: str, sv_name: str):
        self.sv_dir = Path(eso_dir) / "SavedVariables"
        self.sv_name = sv_name
        self.sv_file = self.sv_dir / f"{sv_name}.lua"
        self._last_hash = None

    def exists(self) -> bool:
        return self.sv_file.exists()

    def has_changed(self) -> bool:
        """Check if the file has changed since last read."""
        if not self.sv_file.exists():
            return False
        current_hash = hashlib.md5(self.sv_file.read_bytes()).hexdigest()
        if current_hash != self._last_hash:
            return True
        return False

    def read(self) -> dict:
        """Parse the SavedVariables Lua file into a Python dict."""
        if not self.sv_file.exists():
            log.warning("SavedVariables file not found: %s", self.sv_file)
            return {}

        content = self.sv_file.read_text(encoding="utf-8", errors="replace")
        self._last_hash = hashlib.md5(content.encode()).hexdigest()

        return self._parse_lua_table(content)

    def _parse_lua_table(self, content: str) -> dict:
        """Simple Lua table parser for SavedVariables format."""
        result = {}

        # Find the main variable assignment: VarName = { ... }
        pattern = rf'{self.sv_name}\s*=\s*'
        match = re.search(pattern, content)
        if not match:
            return result

        # Extract the table content
        start = content.find("{", match.end())
        if start == -1:
            return result

        try:
            table_str = self._extract_table(content, start)
            result = self._lua_to_python(table_str)
        except Exception as e:
            log.error("Failed to parse SavedVariables: %s", e)

        return result

    def _extract_table(self, content: str, start: int) -> str:
        """Extract a balanced {} table from content."""
        depth = 0
        i = start
        while i < len(content):
            if content[i] == "{":
                depth += 1
            elif content[i] == "}":
                depth -= 1
                if depth == 0:
                    return content[start:i + 1]
            i += 1
        return content[start:]

    def _lua_to_python(self, lua_str: str) -> dict:
        """Convert a Lua table string to Python dict.
        Handles the subset used by ESO SavedVariables."""
        # Strip outer braces
        lua_str = lua_str.strip()
        if lua_str.startswith("{") and lua_str.endswith("}"):
            lua_str = lua_str[1:-1]

        result = {}
        i = 0
        while i < len(lua_str):
            # Skip whitespace and commas
            while i < len(lua_str) and lua_str[i] in " \t\n\r,":
                i += 1
            if i >= len(lua_str):
                break

            # Skip comments
            if lua_str[i:i+2] == "--":
                while i < len(lua_str) and lua_str[i] != "\n":
                    i += 1
                continue

            # ["key"] = value
            if lua_str[i] == "[":
                key_end = lua_str.find("]", i)
                if key_end == -1:
                    break
                key_str = lua_str[i+1:key_end].strip().strip('"').strip("'")
                i = key_end + 1
                # Skip = 
                while i < len(lua_str) and lua_str[i] in " \t\n\r=":
                    i += 1
                value, i = self._parse_value(lua_str, i)
                try:
                    key = int(key_str)
                except ValueError:
                    key = key_str
                result[key] = value
            else:
                # key = value (bare identifier)
                eq_pos = lua_str.find("=", i)
                if eq_pos == -1:
                    break
                key = lua_str[i:eq_pos].strip()
                i = eq_pos + 1
                while i < len(lua_str) and lua_str[i] in " \t\n\r":
                    i += 1
                value, i = self._parse_value(lua_str, i)
                result[key] = value

        return result

    def _parse_value(self, s: str, i: int):
        """Parse a Lua value starting at position i."""
        while i < len(s) and s[i] in " \t\n\r":
            i += 1
        if i >= len(s):
            return None, i

        # String
        if s[i] == '"':
            end = i + 1
            while end < len(s):
                if s[end] == "\\" and end + 1 < len(s):
                    end += 2
                    continue
                if s[end] == '"':
                    return s[i+1:end], end + 1
                end += 1
            return s[i+1:], len(s)

        # Table
        if s[i] == "{":
            table_str = self._extract_table(s, i)
            val = self._lua_to_python(table_str)
            return val, i + len(table_str)

        # Boolean / nil
        if s[i:i+4] == "true":
            return True, i + 4
        if s[i:i+5] == "false":
            return False, i + 5
        if s[i:i+3] == "nil":
            return None, i + 3

        # Number
        end = i
        while end < len(s) and s[end] not in " \t\n\r,}]":
            end += 1
        num_str = s[i:end]
        try:
            return int(num_str), end
        except ValueError:
            try:
                return float(num_str), end
            except ValueError:
                return num_str, end

    def write_incoming(self, data: dict):
        """Write server data into the SavedVariables for the addon to read.
        Merges into the existing file's 'incoming' section."""
        if not self.sv_file.exists():
            log.warning("SavedVariables file doesn't exist yet. Skipping write.")
            return

        content = self.sv_file.read_text(encoding="utf-8", errors="replace")

        # Build the incoming Lua table
        incoming_lua = self._python_to_lua(data, indent=2)

        # Check if incoming section exists
        incoming_pattern = r'(\["incoming"\]\s*=\s*)\{[^}]*(?:\{[^}]*\}[^}]*)*\}'
        if re.search(incoming_pattern, content):
            content = re.sub(incoming_pattern, f'["incoming"] = {incoming_lua}', content, count=1)
        else:
            # Insert before the last closing brace of the main table
            last_brace = content.rfind("}")
            if last_brace > 0:
                insertion = f'\t["incoming"] = {incoming_lua},\n'
                content = content[:last_brace] + insertion + content[last_brace:]

        self.sv_file.write_text(content, encoding="utf-8")

    def clear_outgoing(self):
        """Clear the outgoing actions after they've been synced."""
        if not self.sv_file.exists():
            return

        content = self.sv_file.read_text(encoding="utf-8", errors="replace")

        # Clear outgoing.ah_actions
        pattern = r'(\["ah_actions"\]\s*=\s*)\{[^}]*(?:\{[^}]*\}[^}]*)*\}'
        if re.search(pattern, content):
            content = re.sub(pattern, r'["ah_actions"] = {}', content)
            self.sv_file.write_text(content, encoding="utf-8")

    def _python_to_lua(self, obj, indent=1) -> str:
        """Convert a Python object to Lua table syntax."""
        pad = "\t" * indent
        pad_inner = "\t" * (indent + 1)

        if obj is None:
            return "nil"
        if isinstance(obj, bool):
            return "true" if obj else "false"
        if isinstance(obj, int):
            return str(obj)
        if isinstance(obj, float):
            return f"{obj:.6f}"
        if isinstance(obj, str):
            escaped = obj.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
            return f'"{escaped}"'
        if isinstance(obj, list):
            if not obj:
                return "{}"
            items = []
            for item in obj:
                items.append(f"{pad_inner}{self._python_to_lua(item, indent + 1)},")
            return "{\n" + "\n".join(items) + f"\n{pad}}}"
        if isinstance(obj, dict):
            if not obj:
                return "{}"
            items = []
            for key, val in obj.items():
                lua_val = self._python_to_lua(val, indent + 1)
                if isinstance(key, int):
                    items.append(f'{pad_inner}[{key}] = {lua_val},')
                else:
                    items.append(f'{pad_inner}["{key}"] = {lua_val},')
            return "{\n" + "\n".join(items) + f"\n{pad}}}"
        return str(obj)


# ---------------------------------------------------------------------------
#  API Client
# ---------------------------------------------------------------------------

class APIClient:
    """HTTP client for the Tamriel Auction House server."""

    def __init__(self, server_url: str, api_key: Optional[str] = None):
        self.server_url = server_url.rstrip("/")
        self.api_key = api_key
        self.session = requests.Session()
        if api_key:
            self.session.headers["X-API-Key"] = api_key

    def set_api_key(self, key: str):
        self.api_key = key
        self.session.headers["X-API-Key"] = key

    def health(self) -> dict:
        resp = self.session.get(f"{self.server_url}/api/v1/health", timeout=5)
        return resp.json()

    def register(self, player_name: str) -> dict:
        resp = self.session.post(
            f"{self.server_url}/api/v1/auth/register",
            json={"player_name": player_name}, timeout=10)
        resp.raise_for_status()
        return resp.json()

    def sync_push(self, actions: list) -> dict:
        resp = self.session.post(
            f"{self.server_url}/api/v1/sync/push",
            json={"actions": actions}, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def sync_pull(self, since: Optional[str] = None) -> dict:
        params = {}
        if since:
            params["since"] = since
        resp = self.session.get(
            f"{self.server_url}/api/v1/sync",
            params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def get_stats(self) -> dict:
        resp = self.session.get(f"{self.server_url}/api/v1/stats", timeout=10)
        resp.raise_for_status()
        return resp.json()


# ---------------------------------------------------------------------------
#  Sync Engine
# ---------------------------------------------------------------------------

class SyncEngine:
    """Main sync loop: watches SavedVariables, pushes/pulls from server."""

    def __init__(self, config: dict):
        self.config = config
        self.api = APIClient(config["server_url"], config.get("api_key"))
        self.sv = SavedVarsManager(config["eso_dir"], config["saved_vars_name"])
        self.last_sync_time = None
        self.player_name = config.get("account_name")
        self.running = False
        self.stats = {"pushes": 0, "pulls": 0, "errors": 0, "listings_synced": 0}
        self._synced_listing_ids = set()  # Track listings already on the server

    def ensure_registered(self):
        """Register with the server if we don't have an API key."""
        if self.config.get("api_key"):
            self.api.set_api_key(self.config["api_key"])
            return

        if not self.player_name:
            # Try to read player name from SavedVariables
            self.player_name = self._detect_player_name()
            if not self.player_name:
                log.error("Cannot determine player name. Log into ESO first.")
                sys.exit(1)

        log.info("Registering player: %s", self.player_name)
        result = self.api.register(self.player_name)
        self.config["api_key"] = result["api_key"]
        self.config["account_name"] = self.player_name
        self.api.set_api_key(result["api_key"])
        log.info("Registered! API key: %s...", result["api_key"][:12])

    def _detect_player_name(self) -> Optional[str]:
        """Try to read the player name from SavedVariables."""
        if not self.sv.exists():
            return None
        data = self.sv.read()
        # SavedVariables are typically keyed by account name
        for key in data:
            if key.startswith("@"):
                return key
            # Check nested structure
            if isinstance(data[key], dict):
                for subkey in data[key]:
                    if isinstance(subkey, str) and subkey.startswith("@"):
                        return subkey
        return None

    def check_server(self) -> bool:
        """Check if the server is reachable."""
        try:
            result = self.api.health()
            return result.get("status") == "healthy"
        except Exception:
            return False

    def push_outgoing(self):
        """Read outgoing actions from SavedVariables and push to server."""
        if not self.sv.exists():
            return

        data = self.sv.read()
        outgoing = self._find_nested(data, "outgoing")

        action_list = []

        # Method 1: Check the explicit action queue
        if outgoing:
            actions = outgoing.get("ah_actions", {})
            if isinstance(actions, dict):
                for _, action in sorted(actions.items(), key=lambda x: str(x[0])):
                    if isinstance(action, dict) and action.get("action"):
                        action_list.append(action)
            elif isinstance(actions, list):
                action_list = [a for a in actions if isinstance(a, dict) and a.get("action")]

        # Method 2: Fallback — scan myListings for unsynced listings
        # This catches listings that were created but the action queue was missed
        if not action_list:
            my_listings = self._find_nested(data, "myListings")
            if my_listings and isinstance(my_listings, dict):
                for lid, listing in my_listings.items():
                    if not isinstance(listing, dict):
                        continue
                    state = listing.get("state", "")
                    if state != "listed":
                        continue

                    # Check if this listing already exists on the server
                    if lid in self._synced_listing_ids:
                        continue

                    # Check against server — try to fetch it
                    try:
                        self.api.session.get(
                            f"{self.api.base_url}/api/v1/listings/{lid}",
                            timeout=5
                        ).raise_for_status()
                        # Listing exists on server, skip
                        self._synced_listing_ids.add(lid)
                        continue
                    except Exception:
                        pass  # Listing not on server, need to push

                    log.info("Found unsynced listing: %s (%s)", lid, listing.get("itemName", "?"))
                    action_list.append({
                        "action": "create",
                        "data": listing,
                        "timestamp": listing.get("createdAt", 0),
                        "player": self.player_name,
                    })

        if not action_list:
            return

        log.info("Pushing %d actions to server...", len(action_list))
        try:
            result = self.api.sync_push(action_list)
            log.info("Push result: %d processed", result.get("processed", 0))
            self.stats["pushes"] += 1

            # Track synced listings so we don't push them again
            for action in action_list:
                data = action.get("data", {})
                lid = data.get("id", "")
                if lid:
                    self._synced_listing_ids.add(lid)

            # Clear the outgoing queue
            self.sv.clear_outgoing()

            # Check for failed actions and write failure notifications
            failed_notifications = []
            for i, r in enumerate(result.get("results", [])):
                if r.get("status") == "error":
                    log.warning("  Action error for %s: %s", r.get("id"), r.get("error"))

                    # Find the original action to get details
                    original = action_list[i] if i < len(action_list) else {}
                    act = original.get("action", "")
                    data = original.get("data", {})

                    if act == "purchase":
                        failed_notifications.append({
                            "type": "purchase_failed",
                            "listing_id": r.get("id", data.get("id", "")),
                            "data": {
                                "reason": r.get("error", "Item already sold or no longer available"),
                                "item_name": data.get("itemName", "Unknown"),
                            },
                            "created_at": "",
                        })
                elif r.get("status") == "already_sold":
                    original = action_list[i] if i < len(action_list) else {}
                    data = original.get("data", {})
                    failed_notifications.append({
                        "type": "purchase_failed",
                        "listing_id": r.get("id", ""),
                        "data": {
                            "reason": "This item was already purchased by another buyer.",
                            "item_name": data.get("itemName", "Unknown"),
                        },
                        "created_at": "",
                    })
                else:
                    log.debug("  %s: %s", r.get("id", "?")[:16], r.get("status"))

            # Write failure notifications to incoming data
            if failed_notifications:
                try:
                    sv_data = self.sv.read()
                    incoming = self._find_nested(sv_data, "incoming") or {}
                    existing_notifs = incoming.get("ah_notifications", [])
                    if isinstance(existing_notifs, dict):
                        existing_notifs = list(existing_notifs.values())
                    existing_notifs.extend(failed_notifications)
                    incoming["ah_notifications"] = existing_notifs
                    self.sv.write_incoming(incoming)
                    log.info("Wrote %d failure notifications", len(failed_notifications))
                except Exception as e:
                    log.error("Failed to write failure notifications: %s", e)

        except requests.exceptions.RequestException as e:
            log.error("Push failed: %s", e)
            self.stats["errors"] += 1

    def pull_incoming(self):
        """Pull listings and notifications from server, write to SavedVariables."""
        try:
            result = self.api.sync_pull(since=self.last_sync_time)
        except requests.exceptions.RequestException as e:
            log.error("Pull failed: %s", e)
            self.stats["errors"] += 1
            return

        listings = result.get("listings", [])
        notifications = result.get("notifications", [])
        self.last_sync_time = result.get("server_time")

        # Convert server listings to addon-compatible format
        incoming_data = {
            "ah_listings": {},
            "ah_purchases": [],
            "ah_notifications": [],
            "sync_time": self.last_sync_time,
            "is_connected": True,
            "total_listings": len(listings),
        }

        for listing in listings:
            lid = listing["id"]
            # Track that this listing is already on the server
            self._synced_listing_ids.add(lid)
            incoming_data["ah_listings"][lid] = {
                "id": lid,
                "itemLink": listing.get("item_link", ""),
                "itemName": listing.get("item_name", ""),
                "itemId": listing.get("item_id", ""),
                "icon": listing.get("icon", ""),
                "quality": listing.get("quality", 0),
                "level": listing.get("level", 0),
                "championPoints": listing.get("champion_points", 0),
                "quantity": listing.get("quantity", 1),
                "price": listing.get("price", 0),
                "unitPrice": listing.get("unit_price", 0),
                "seller": listing.get("seller", ""),
                "sellerOnline": listing.get("seller_online", False),
                "buyer": listing.get("buyer"),
                "state": listing.get("state", "listed"),
                "expiresAt": listing.get("time_remaining", 0),
                "timeRemaining": listing.get("time_remaining", 0),
            }

        # Process purchase notifications
        for notif in notifications:
            if notif["type"] == "item_sold":
                incoming_data["ah_purchases"].append({
                    "listingId": notif["listing_id"],
                    "buyer": notif["data"].get("buyer", ""),
                    "seller": self.player_name,
                    "state": "awaiting_cod",
                    "itemName": notif["data"].get("item_name", ""),
                    "price": notif["data"].get("price", 0),
                    "quantity": notif["data"].get("quantity", 1),
                })

            # Pass all notifications through so the addon can handle them
            incoming_data["ah_notifications"].append({
                "type": notif["type"],
                "listing_id": notif.get("listing_id", ""),
                "data": notif.get("data", {}),
                "created_at": notif.get("created_at", ""),
            })

        # Write to SavedVariables
        self.sv.write_incoming(incoming_data)

        self.stats["pulls"] += 1
        self.stats["listings_synced"] = len(listings)

        if listings or notifications:
            log.info("Pulled %d listings, %d notifications",
                     len(listings), len(notifications))

    def _find_nested(self, data: dict, key: str):
        """Find a key in a potentially nested SavedVariables structure."""
        if key in data:
            return data[key]
        for v in data.values():
            if isinstance(v, dict):
                result = self._find_nested(v, key)
                if result is not None:
                    return result
        return None

    def run(self):
        """Main sync loop."""
        log.info("=" * 60)
        log.info("  Tamriel Auction House — Desktop Client")
        log.info("=" * 60)
        log.info("Server: %s", self.config["server_url"])
        log.info("ESO Dir: %s", self.config["eso_dir"])
        log.info("Sync interval: %ds", self.config["sync_interval"])
        log.info("")

        # Check server
        if not self.check_server():
            log.error("Server not reachable at %s", self.config["server_url"])
            log.error("Make sure the server is running and try again.")
            sys.exit(1)
        log.info("Server connected!")

        # Register
        self.ensure_registered()
        log.info("Player: %s", self.player_name or "Unknown")
        log.info("")
        log.info("Watching for changes... (Ctrl+C to stop)")
        log.info("")

        self.running = True
        cycle = 0
        try:
            while self.running:
                cycle += 1

                # Push outgoing actions
                if self.sv.has_changed():
                    self.push_outgoing()

                # Pull incoming data
                self.pull_incoming()

                # Periodic stats
                if cycle % 60 == 0:
                    log.info("Stats — Pushes: %d | Pulls: %d | Errors: %d | Active listings: %d",
                             self.stats["pushes"], self.stats["pulls"],
                             self.stats["errors"], self.stats["listings_synced"])

                time.sleep(self.config["sync_interval"])

        except KeyboardInterrupt:
            log.info("\nShutting down...")
            self.running = False


# ---------------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Tamriel Auction House Desktop Client")
    parser.add_argument("--config", default="tah_config.json", help="Config file path")
    parser.add_argument("--server", help="Server URL (e.g. http://your-server:8000)")
    parser.add_argument("--eso-dir", help="ESO documents directory path")
    parser.add_argument("--player", help="Player name (e.g. @YourName)")
    args = parser.parse_args()

    config = load_config(args.config)

    if args.server:
        config["server_url"] = args.server
    if args.eso_dir:
        config["eso_dir"] = args.eso_dir
    if args.player:
        config["account_name"] = args.player

    engine = SyncEngine(config)
    engine.run()

    # Save config (with API key) for next run
    save_config(config, args.config)


if __name__ == "__main__":
    main()
