# linkedin/db/profiles.py
import json
import logging
from typing import Dict, Any, Optional
from typing import List
from urllib.parse import urlparse, unquote

import pandas as pd
from sqlalchemy import func
from termcolor import colored

from linkedin.db.models import Profile
from linkedin.navigation.enums import ProfileState

logger = logging.getLogger(__name__)


def add_profile_urls(session: "AccountSession", urls: List[str]):
    if not urls:
        return

    public_ids = {pid for url in urls if (pid := url_to_public_id(url))}
    if not public_ids:
        return

    db = session.db_session
    
    # Cloud Postgres & SQLite friendly insert
    existing_ids = {row.public_identifier for row in db.query(Profile.public_identifier).filter(Profile.public_identifier.in_(public_ids)).all()}
    new_ids = public_ids - existing_ids
    
    if new_ids:
        db.bulk_save_objects([Profile(public_identifier=pid, state=ProfileState.DISCOVERED.value) for pid in new_ids])
        db.commit()

    logger.debug(f"Discovered {len(public_ids)} unique LinkedIn profiles")


def save_scraped_profile(
        session: "AccountSession",
        url: str,
        profile: Dict[str, Any],
        data: Optional[Dict[str, Any]] = None,
):
    from linkedin.conf import ASSETS_DIR
    import csv 

    public_id = url_to_public_id(url)
    if not public_id:
        logger.warning(f"Invalid LinkedIn URL, cannot save profile: {url}")
        return

    db = session.db_session

    # Get existing or create new instance
    profile_db = db.get(Profile, public_id)
    if profile_db is None:
        profile_db = Profile(public_identifier=public_id)
        db.add(profile_db)
        logger.debug(f"New profile created in DB: {public_id}")
    else:
        logger.debug(f"Updating existing profile: {public_id}")

    # Now safely update fields
    profile_db.profile = profile
    profile_db.data = data
    profile_db.cloud_synced = False
    # Force re-sync on next close()
    profile_db.updated_at = func.now()
    profile_db.state = ProfileState.ENRICHED.value

    db.commit()

    debug_profile_preview(profile) if logger.isEnabledFor(logging.DEBUG) else None

    logger.debug(f"SUCCESS: Saved enriched profile → {public_id}")
    
    # --- AUTO-SAVE TO CSV ---
    try:
        csv_path = ASSETS_DIR / "candidates_detailed.csv"
        file_exists = csv_path.exists()
        
        # Flatten Data
        # Flatten Data
        flat_data = {
            "public_id": public_id,
            "url": url,
            "full_name": profile.get("full_name", ""),
            "headline": profile.get("headline", ""),
            "location": profile.get("location_name", ""),
            "summary": profile.get("summary", ""),
            "about": profile.get("about", ""),
            "email": profile.get("email", ""),
            "phone": profile.get("phone", ""),
            
            # Extract Current Job & Company Details
            "current_company_name": "",
            "current_job_title": "",
            "current_job_date_range": "",
            "company_description": "",
            "company_website": "",
            "company_industry": "",
            "company_size": "",
            "company_headquarters": "",
            "company_specialties": "",
            
            "all_positions_summary": "" # Detailed dump of all positions
        }
        
        positions = profile.get("positions", [])
        if positions and isinstance(positions, list) and len(positions) > 0:
            current_job = positions[0]
            flat_data["current_company_name"] = current_job.get("company_name", "")
            flat_data["current_job_title"] = current_job.get("title", "")
            
            # Format Date Range
            dr = current_job.get("date_range", {})
            if dr:
                start = dr.get("start", {})
                end = dr.get("end", {})
                start_str = f"{start.get('month', '?')}/{start.get('year', '?')}" if start else "N/A"
                end_str = f"{end.get('month', '?')}/{end.get('year', '?')}" if end else "Present"
                flat_data["current_job_date_range"] = f"{start_str} - {end_str}"

            # Company Details (if enriched)
            comp_details = current_job.get("company_details", {})
            if comp_details:
                flat_data["company_description"] = comp_details.get("description", "")
                flat_data["company_website"] = comp_details.get("url", "")
                flat_data["company_industry"] = comp_details.get("industry", "")
                flat_data["company_size"] = comp_details.get("employee_count", "")
                flat_data["company_headquarters"] = comp_details.get("headquarters", "")
                flat_data["company_specialties"] = ", ".join(comp_details.get("specialties", []))

            # Build All Positions Summary
            summary_parts = []
            for p in positions:
                p_title = p.get("title", "Unknown")
                p_comp = p.get("company_name", "Unknown")
                p_desc = p.get("company_details", {}).get("description", "No info available")
                summary_parts.append(f"[{p_title} @ {p_comp}]: {p_desc}")
            flat_data["all_positions_summary"] = " | ".join(summary_parts)

        # Build Transcript
        try:
            transcript_parts = []
            if profile_db.messages:
                for m in profile_db.messages:
                    ts = m.timestamp.strftime("%Y-%m-%d %H:%M") if m.timestamp else "Unknown"
                    transcript_parts.append(f"[{ts}] {m.sender_name}: {m.text}")
            flat_data["transcript"] = "\n".join(transcript_parts)
        except Exception as e:
            logger.debug(f"Could not build transcript for CSV: {e}")
            flat_data["transcript"] = ""

        fields = [
            "public_id", "url", "full_name", "headline", "location", 
            "current_company_name", "current_job_title", "current_job_date_range",
            "company_description", "company_website", "company_industry", 
            "company_size", "company_headquarters", "company_specialties",
            "summary", "about", "email", "phone", "all_positions_summary", "transcript"
        ]
        
        with open(csv_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            if not file_exists:
                writer.writeheader()
            writer.writerow(flat_data)
            
        logger.info(f"✅ Auto-saved enriched profile (with company details) to CSV: {public_id}")
        
    except Exception as e:
        logger.error(f"Failed to auto-save to CSV: {e}")


def get_next_url_to_scrape(session: "AccountSession", limit: int = 1) -> List[str]:
    rows = (session.db_session
            .query(Profile.public_identifier)
            .filter(Profile.state == ProfileState.DISCOVERED.value)
            .limit(limit)
            .all())
    return [public_id_to_url(row.public_identifier) for row in rows]


def count_pending_scrape(session: "AccountSession") -> int:
    return (session.db_session
            .query(Profile)
            .filter(Profile.state == ProfileState.DISCOVERED.value)
            .count())


def url_to_public_id(url: str) -> str:
    """
    Strict LinkedIn public ID extractor:
    - Path MUST start with /in/
    - Returns the second segment, percent-decoded
    - Anything else → raises ValueError
    """
    if not url:
        raise ValueError("Empty URL")

    path = urlparse(url.strip()).path
    parts = path.strip("/").split("/")

    if len(parts) < 2 or parts[0] != "in":
        raise ValueError(f"Not a valid /in/ profile URL: {url!r}")

    public_id = parts[1]
    return unquote(public_id)


def public_id_to_url(public_id: str) -> str:
    """
    Convert public_identifier back to a clean LinkedIn profile URL.

    You can choose www or not — both work, www is slightly more common.
    """
    if not public_id:
        return ""
    public_id = public_id.strip("/")
    return f"https://www.linkedin.com/in/{public_id}/"


def get_profile_from_url(session: "AccountSession", url: str):
    public_identifier = url_to_public_id(url)
    if not public_identifier:
        return None

    return get_profile(session, public_identifier)


def get_profile(session: "AccountSession", public_identifier: str) -> Any:
    return session.db_session \
        .query(Profile) \
        .filter_by(public_identifier=public_identifier) \
        .first()


def set_profile_state(session: "AccountSession", public_identifier, new_state: str):
    db = session.db_session
    row = db.get(Profile, public_identifier)
    if not row:
        row = Profile(public_identifier=public_identifier, state=new_state)
        db.add(row)
    else:
        row.state = new_state
    db.commit()

    log_msg = None
    match new_state:
        case ProfileState.DISCOVERED:
            log_msg = colored("DISCOVERED", "green")
        case ProfileState.ENRICHED:
            log_msg = colored("ENRICHED", "yellow", attrs=["bold"])
        case ProfileState.PENDING:
            log_msg = colored("PENDING", "yellow", attrs=["bold"])
        case ProfileState.CONNECTED:
            log_msg = colored("CONNECTED", "green")
        case ProfileState.COMPLETED:
            log_msg = colored("COMPLETED", "green", attrs=["bold"])
        case _:
            log_msg = colored("ERROR", "red", attrs=["bold"])

    logger.info(f"{public_identifier} {log_msg}")


def save_message_sent(session: "AccountSession", public_identifier: str, message: str, job_id: str = None):
    from linkedin.db.models import MessageEntry
    db = session.db_session
    row = db.get(Profile, public_identifier)
    if row:
        row.last_message = message
        row.last_message_at = func.now()
        row.state = ProfileState.COMPLETED.value
        
        if job_id:
            row.last_job_id = job_id
        
        # Add to history
        entry = MessageEntry(
            profile_id=public_identifier,
            direction="outgoing",
            text=message,
            sender_name="You"
        )
        db.add(entry)
        
        db.commit()
        logger.info(f"✅ Logged message sent to {public_identifier}")
        
        # Trigger AI Analysis
        from linkedin.db.analytics import analyze_conversation
        analyze_conversation(db, public_identifier)


def save_conversation_history(session: "AccountSession", public_identifier: str, messages: List[Dict[str, str]]):
    """
    Save multiple messages from a conversation. Deduplicates based on text.
    """
    from linkedin.db.models import MessageEntry
    from sqlalchemy import func
    
    db = session.db_session
    profile_db = db.get(Profile, public_identifier)
    if not profile_db or not messages:
        return

    # Get existing message texts to avoid duplicates
    existing_texts = {m.text for m in profile_db.messages}
    
    new_count = 0
    for msg in messages:
        text = msg.get("text")
        if text and text not in existing_texts:
            sender = msg.get("sender", "Unknown")
            # Heuristic to detect direction: if sender is NOT 'You', it's incoming
            direction = "incoming" if sender != "You" else "outgoing"
            
            entry = MessageEntry(
                profile_id=public_identifier,
                direction=direction,
                text=text,
                sender_name=sender
            )
            db.add(entry)
            new_count += 1
            
            # Update last state if it's the most recent incoming
            if direction == "incoming":
                profile_db.last_received_message = text
                profile_db.last_received_at = func.now()

    if new_count > 0:
        db.commit()
        logger.info(f"🧬 Synced {new_count} new messages for {public_identifier}")
        
        # Trigger AI Analysis
        from linkedin.db.analytics import analyze_conversation
        analyze_conversation(db, public_identifier)
    else:
        logger.debug(f"No new messages to sync for {public_identifier}")


def save_received_message(session: "AccountSession", public_identifier: str, message: str):
    from linkedin.db.models import MessageEntry
    from sqlalchemy import func
    db = session.db_session
    row = db.get(Profile, public_identifier)
    if row:
        # Update last state
        if row.last_received_message != message:
            row.last_received_message = message
            row.last_received_at = func.now()
            
            # Add to history
            entry = MessageEntry(
                profile_id=public_identifier,
                direction="incoming",
                text=message,
                sender_name=row.profile.get("full_name") or row.profile.get("name") or "Candidate"
            )
            db.add(entry)
            
            db.commit()
            logger.info(f"📩 Logged interaction from {public_identifier}: {message[:30]}...")

            # Trigger AI Analysis
            from linkedin.db.analytics import analyze_conversation
            analyze_conversation(db, public_identifier)


def debug_profile_preview(enriched):
    pretty = json.dumps(enriched, indent=2, ensure_ascii=False, default=str)
    preview_lines = pretty.splitlines()[:3]
    logger.debug("=== ENRICHED PROFILE PREVIEW ===\n%s\n...", '\n'.join(preview_lines))


def get_updated_at_df(session: "AccountSession", public_identifiers: List[str]) -> pd.DataFrame:
    """
    Return a DataFrame with public_identifier and updated_at for existing profiles.
    GLOBAL CHECK: Scans ALL account databases to prevent overlapping outreach!
    """
    if not public_identifiers:
        return pd.DataFrame(columns=["public_identifier", "updated_at"])

    from linkedin.conf import DATA_DIR
    from linkedin.db.engine import Database
    import glob
    import os

    all_results = []
    
    # 1. Glob all .db files in assets/data
    db_files = glob.glob(str(DATA_DIR / "*.db"))
    
    # 2. Query each DB for these specific public_identifiers
    for db_path in db_files:
        try:
            if not os.path.isfile(db_path): continue
            
            # Temporary session for this DB
            temp_db = Database(db_path)
            s = temp_db.get_session()
            rows = (
                s.query(Profile.public_identifier, Profile.updated_at)
                .filter(Profile.public_identifier.in_(public_identifiers))
                .all()
            )
            if rows:
                all_results.extend(rows)
            s.close()
        except Exception as e:
            logger.debug(f"Failed to scan global DB {db_path}: {e}")

    if not all_results:
        return pd.DataFrame(columns=["public_identifier", "updated_at"])

    df = pd.DataFrame(all_results, columns=["public_identifier", "updated_at"])
    
    # Since a profile might exist in multiple DBs, group by ID and keep the newest date
    df = df.groupby("public_identifier", as_index=False).agg({"updated_at": "max"})

    if len(df) > 0:
        logger.info(colored(f"🛡️ GLOBAL SAFETY: Found {len(df)} profiles already processed across other accounts!", "magenta"))

    return df
