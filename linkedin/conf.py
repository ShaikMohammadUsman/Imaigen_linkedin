# linkedin/conf.py
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Any, List

import yaml
from dotenv import load_dotenv

load_dotenv()

# ----------------------------------------------------------------------
# Global OpenAI config
# ----------------------------------------------------------------------
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
AI_MODEL = os.getenv("AI_MODEL", "gpt-4o-mini")

# Azure OpenAI Config
AZURE_OPENAI_KEY = os.getenv("AZURE_OPENAI_KEY")
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AZURE_DEPLOYMENT_NAME = os.getenv("AZURE_DEPLOYMENT_NAME")
AZURE_API_VERSION = os.getenv("AZURE_API_VERSION")

# ----------------------------------------------------------------------
# Paths (all under assets/)
# ----------------------------------------------------------------------
ROOT_DIR = Path(__file__).parent.parent
ASSETS_DIR = ROOT_DIR / "assets"

COOKIES_DIR = ASSETS_DIR / "cookies"
DATA_DIR = ASSETS_DIR / "data"

COOKIES_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(exist_ok=True)

FIXTURE_DIR = ROOT_DIR / "tests" / "fixtures"
FIXTURE_PROFILES_DIR = FIXTURE_DIR / "profiles"
FIXTURE_PAGES_DIR = FIXTURE_DIR / "pages"

MIN_DELAY = 30  # Profile-to-Profile delay (safe: 30-75s)
MAX_DELAY = 75

MIN_UI_DELAY = 1.5  # Button clicks, small steps
MAX_UI_DELAY = 4.0

OPPORTUNISTIC_SCRAPING = False

# ----------------------------------------------------------------------
# SINGLE secrets file
# ----------------------------------------------------------------------
SECRETS_PATH = ASSETS_DIR / "accounts.secrets.yaml"

if not SECRETS_PATH.exists():
    raise FileNotFoundError(
        f"\nMissing config file: {SECRETS_PATH}\n"
        "→ cp assets/accounts.secrets.template.yaml assets/accounts.secrets.yaml\n"
        "  and fill in your accounts (public settings + credentials)\n"
    )

def load_secrets():
    """Reloads the secrets YAML file from disk."""
    if not SECRETS_PATH.exists():
        return {}
    with open(SECRETS_PATH, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return raw.get("accounts", {})

def get_account_config(handle: str) -> Dict[str, Any]:
    # Always get fresh config from disk for dynamic UI updates
    accounts_config = load_secrets()
    if handle not in accounts_config:
        raise KeyError(f"Account '{handle}' not found in {SECRETS_PATH}")

    acct = accounts_config[handle]

    input_csv_rel = acct.get("input_csv")
    followup_rel  = acct.get("followup_template")
    followup_type = acct.get("followup_template_type")
    
    # NEW: Connection Request Note Templates
    connect_rel   = acct.get("connection_template", "templates/prompts/connect.j2")
    connect_type  = acct.get("connection_template_type", "ai_prompt")

    if input_csv_rel is None:
        raise ValueError(f"Missing 'input_csv' for account '{handle}'")
    if followup_rel is None:
        raise ValueError(f"Missing 'followup_template' for account '{handle}'")
    if followup_type is None:
        raise ValueError(f"Missing 'followup_template_type' for account '{handle}'")

    account_db_path = DATA_DIR / f"{handle}.db"

    return {
        "handle": handle,
        "active": acct.get("active", True),
        "username": acct.get("username"),
        "password": acct.get("password"),
        "subscribe_newsletter": acct.get("subscribe_newsletter", None),
        "booking_link": acct.get("booking_link"),

        "cookie_file": COOKIES_DIR / f"{handle}.json",
        "db_path": account_db_path,

        "input_csv": ASSETS_DIR / input_csv_rel,
        "followup_template": ASSETS_DIR / followup_rel,
        "followup_template_type": followup_type,
        
        # NEW: Connection Request Note Templates
        "connection_template": ASSETS_DIR / connect_rel,
        "connection_template_type": connect_type,
    }

def list_active_accounts() -> List[str]:
    """Return list of active account handles (order preserved from YAML)."""
    accounts_config = load_secrets()
    return [
        handle for handle, cfg in accounts_config.items()
        if cfg.get("active", True)
    ]


def get_first_active_account() -> str | None:
    """
    Return the first active account handle from the config, or None if no active accounts.

    The order is deterministic: it follows the insertion order in accounts.secrets.yaml
    (YAML dictionaries preserve order since Python 3.7+).
    """
    active = list_active_accounts()
    return active[0] if active else None


def get_first_account_config() -> Dict[str, Any] | None:
    """
    Return the complete config dict for the first active account, or None if none exist.
    """
    handle = get_first_active_account()
    if handle is None:
        return None
    return get_account_config(handle)


# ----------------------------------------------------------------------
# Debug output when run directly
# ----------------------------------------------------------------------
if __name__ == "__main__":
    print("LinkedIn Automation – Active accounts")
    print(f"Config file : {SECRETS_PATH}")
    print(f"Databases stored in: {DATA_DIR}")
    print("-" * 60)

    active_handles = list_active_accounts()
    if not active_handles:
        print("No active accounts found.")
    else:
        for handle in active_handles:
            cfg = get_account_config(handle)
            status = "ACTIVE" if cfg["active"] else "inactive"
            print(f"{status} • {handle}")
            print("  Config values:")
            for key, value in cfg.items():
                # Make paths prettier and handle None
                if isinstance(value, Path):
                    value = value.as_posix()
                elif value is None:
                    value = "null"
                print(f"    {key.ljust(20)} : {value}")
            print()

        print("-" * 60)
        first = get_first_active_account()
        print(f"First active account → {first or 'None'}")