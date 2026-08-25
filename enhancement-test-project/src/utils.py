# ──────────────────────────────────────────
# Utility Functions
# ──────────────────────────────────────────
"""
Shared utilities for the order service.
Has platform-specific issues (Windows path handling, encoding).
"""

import os
import sys
import json
import hashlib
from pathlib import Path
from typing import Any


def load_config(config_name: str = "settings") -> dict:
    """
    Load configuration from JSON file.
    BUG: Uses hardcoded unix-style path separator (tests Enhancement 5).
    """
    config_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # Intentional bug: hardcoded forward slash on Windows
    config_path = config_dir + "/config/" + config_name + ".json"
    
    if not os.path.exists(config_path):
        return {"debug": False, "log_level": "INFO"}
    
    with open(config_path, "r") as f:
        return json.load(f)


def format_currency(amount: float, currency: str = "USD") -> str:
    """Format amount as currency string."""
    symbols = {"USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥"}
    symbol = symbols.get(currency, currency)
    return f"{symbol}{amount:,.2f}"


def hash_order(order: dict) -> str:
    """Generate a hash for an order (for deduplication)."""
    content = json.dumps(order, sort_keys=True)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


def get_log_path() -> Path:
    """Get the log file path. Platform-aware."""
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("LOCALAPPDATA", "C:\\Temp"))
    else:
        base = Path("/var/log")
    return base / "order-service" / "app.log"


def write_log(message: str, level: str = "INFO"):
    """Write a log message to the log file."""
    log_path = get_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(log_path, "a", encoding="utf-8") as f:
        from datetime import datetime
        timestamp = datetime.now().isoformat()
        f.write(f"[{timestamp}] [{level}] {message}\n")


def validate_email(email: str) -> bool:
    """Basic email validation."""
    import re
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, email))
