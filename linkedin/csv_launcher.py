# linkedin/csv_launcher.py
import logging
from pathlib import Path
from typing import Optional

import pandas as pd

from termcolor import colored
from linkedin.campaigns.engine import start_campaign
from linkedin.conf import get_first_active_account
from linkedin.db.profiles import get_updated_at_df
from linkedin.db.profiles import url_to_public_id
from linkedin.sessions.registry import get_session

logger = logging.getLogger(__name__)


def load_profiles_df(csv_path: Path | str):
    csv_path = Path(csv_path)
    print(f"📂 [BOT] Loading CSV with ROBUST parser: {csv_path.name}")
    if not csv_path.is_file():
        raise FileNotFoundError(f"CSV file not found: {csv_path}")

    import csv
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, [])
        # Fix: ensure 'source' exists if not already in header (matches ui_server logic)
        if len(header) > 0 and 'source' not in header:
            header.append('source')
        data = []
        for row in reader:
            # Ensure every row has the same number of columns as the header
            while len(row) < len(header):
                row.append("")
            data.append(row[:len(header)])
            
    df = pd.DataFrame(data, columns=header)

    possible_cols = ["url", "linkedin_url", "profile_url"]
    url_column = next(
        (col for col in df.columns if col.lower() in [c.lower() for c in possible_cols]),
        None,
    )

    if url_column is None:
        raise ValueError(f"No URL column found. Available: {list(df.columns)}")

    # Clean, dedupe, keep as DataFrame
    # Clean URL column but keep other data
    df[url_column] = (
        df[url_column]
        .astype(str)
        .str.strip()
    )
    
    urls_df = (
        df
        .replace({"nan": None, "<NA>": None})
        .dropna(subset=[url_column])
        .drop_duplicates(subset=[url_column])
    )

    # Add public identifier
    urls_df["public_identifier"] = urls_df[url_column].apply(url_to_public_id)
    logger.debug(f"First 10 rows of {csv_path.name}:\n"
                 f"{urls_df.head(10).to_string(index=False)}"
                 )
    logger.info(f"Loaded {len(urls_df):,} pristine LinkedIn profile URLs")
    return urls_df


def sort_profiles(session: "AccountSession", profiles_df: pd.DataFrame) -> list:
    """
    Return a new DataFrame sorted by updated_at (oldest first).
    Profiles not in the database come first.
    """
    if profiles_df.empty:
        return []

    public_ids = profiles_df["public_identifier"].tolist()

    # Get DB timestamps as DataFrame
    db_df = get_updated_at_df(session, public_ids)

    # Left join: keep all input profiles
    merged = profiles_df.merge(db_df, on="public_identifier", how="left")

    # Sentinel for profiles not in DB
    sentinel = pd.Timestamp("1970-01-01 00:00:00")

    # Force datetime conversion first + fillna
    merged["updated_at"] = (
        pd.to_datetime(merged["updated_at"], errors="coerce")
        .fillna(sentinel)
    )

    # Sort: oldest (including new profiles) first
    sorted_df = merged.sort_values(by="updated_at").drop(columns="updated_at")

    logger.debug(f"Sorted:\n"
                 f"{sorted_df.head(10).to_string(index=False)}"
                 )
    not_in_db = (merged["updated_at"] == sentinel).sum()
    logger.info(
        f"Sorted {len(sorted_df):,} profiles by last updated: "
        f"{not_in_db} new, {len(sorted_df) - not_in_db} existing (oldest first)"
    )
    return sorted_df.to_dict(orient="records")


def launch_connect_follow_up_campaign(
        handle: Optional[str] = None,
        enrich_only: bool = False,
        limit: int = 20,
        urls: Optional[list[str]] = None,
        note: Optional[str] = None,
):
    """
    One-liner to run the connect → follow-up campaign.
    """
    if handle is None:
        handle = get_first_active_account()
        if handle is None:
            raise RuntimeError(
                "No handle provided and no active accounts found in assets/accounts.secrets.yaml. "
                "Please either pass a handle explicitly or add at least one active account."
            )
        logger.info(f"No handle chosen → auto-picking the boss account: @{handle}")

    session = get_session(
        handle=handle,
    )

    input_csv = session.config['input_csv']
    logger.info(f"🚀 [INIT] Outreach started for @{handle}")
    logger.info(f"📂 Mode: {'Enrichment only' if enrich_only else 'Full Outreach'} | Limit: {limit}")
    import sys
    sys.stdout.flush()
    logger.info(f"Launching campaign → running as @{handle} | CSV: {input_csv} | Enrich Mode: {enrich_only} | Limit: {limit} | Note: {'Provided' if note else 'None'}")

    profiles_df = load_profiles_df(input_csv)
    
    if urls:
        # Filter profiles to only include the selected URLs
        from urllib.parse import urlparse
        def norm(u):
            if not u: return ""
            try:
                # 1. Clean up spacing and lower
                u = str(u).strip().lower()
                # 2. Parse URL
                p = urlparse(u)
                # 3. Handle relative URLs by adding dummy domain if needed
                if not p.netloc:
                    p = urlparse(f"https://www.linkedin.com/in/{u.strip('/')}")
                
                # 4. Remove www. and trailing slashes for uniform matching
                netloc = p.netloc.replace("www.", "")
                path = p.path.rstrip("/")
                return f"{netloc}{path}"
            except:
                return str(u).strip().lower().rstrip("/")
        
        norm_targets = {norm(u) for u in urls}
        logger.debug(f"Target Norms: {norm_targets}")
        
        def match_row(row):
            for col in profiles_df.columns:
                if 'url' in col.lower():
                    val = row[col]
                    if val and norm(val) in norm_targets:
                        return True
            return False

        original_count = len(profiles_df)
        profiles_df = profiles_df[profiles_df.apply(match_row, axis=1)]
        logger.info(colored(f"🎯 Filtered to {len(profiles_df)} selected candidates (out of {original_count} in CSV).", "green"))
        
        if len(profiles_df) == 0:
            logger.warning(colored("⚠️ No matching profiles found in CSV for the selected URLs!", "yellow", attrs=["bold"]))
            logger.info(f"CSV Columns: {list(profiles_df.columns)}")
            logger.info(f"Requested URLs (first 2): {urls[:2]}")
            sys.stdout.flush()

    profiles = sort_profiles(session, profiles_df)
    
    # Inject custom note if provided
    if note:
        for p in profiles:
            p['note'] = note

    logger.info(f"Loaded {len(profiles):,} profiles from CSV – ready for battle!")

    start_campaign(handle, session, profiles, enrich_only=enrich_only, limit=limit)
    
    # Gracefully shut down the browser to prevent Node EPIPE broken pipe crash at exit
    try:
        session.close()
    except Exception as e:
        logger.debug(f"Error closing session: {e}")


def checkpoint(handle: str):
    """
    Open the browser and stay open for manual login/verification.
    """
    from linkedin.navigation.login import manual_login_checkpoint
    
    success = manual_login_checkpoint(handle)
    if success:
        logger.info(colored(f"✅ Checkpoint complete for @{handle}", "green", attrs=["bold"]))
    else:
        logger.error(colored(f"❌ Checkpoint failed for @{handle}", "red", attrs=["bold"]))
