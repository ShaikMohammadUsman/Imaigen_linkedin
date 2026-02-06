#!/usr/bin/env python3
# check_replies.py
"""
Automated Reply Checker - Scans all COMPLETED profiles for new incoming messages.
Runs every 30 minutes to check if candidates have replied.
"""
import logging
import sys
from pathlib import Path

from linkedin.actions.chat import fetch_latest_messages
from linkedin.db.engine import Database
from linkedin.db.models import Profile
from linkedin.db.profiles import save_received_message
from linkedin.navigation.enums import ProfileState
from linkedin.sessions.registry import get_session

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s │ %(levelname)-8s │ %(message)s',
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def check_replies_for_account(handle: str):
    """
    Check all COMPLETED profiles for new replies.
    """
    logger.info(f"🔍 Checking replies for @{handle}...")
    
    # Get database session
    db_wrapper = Database.from_handle(handle)
    db_session = db_wrapper.get_session()
    
    try:
        # Find all COMPLETED profiles (those we've sent messages to)
        completed_profiles = db_session.query(Profile).filter(
            Profile.state == ProfileState.COMPLETED.value
        ).all()
        
        if not completed_profiles:
            logger.info("No completed profiles to check.")
            return
        
        logger.info(f"Found {len(completed_profiles)} completed profiles to check.")
        
        # Get browser session
        session = get_session(handle)
        session.ensure_browser()
        
        checked_count = 0
        new_replies_count = 0
        
        for profile_row in completed_profiles:
            try:
                profile_data = profile_row.profile
                if not profile_data:
                    continue
                
                # Handle stringified JSON
                if isinstance(profile_data, str):
                    import json
                    try:
                        profile_data = json.loads(profile_data)
                    except:
                        continue
                
                public_id = profile_row.public_identifier
                full_name = profile_data.get("full_name") or profile_data.get("name")
                url = f"https://www.linkedin.com/in/{public_id}/"
                
                if not full_name:
                    logger.warning(f"Skipping {public_id} - no name found")
                    continue
                
                profile = {
                    "public_identifier": public_id,
                    "full_name": full_name,
                    "url": url
                }
                
                logger.info(f"Checking {full_name} ({public_id})...")
                
                # Fetch latest messages
                messages = fetch_latest_messages(handle, profile, limit=10)
                
                if not messages:
                    logger.debug(f"No messages found for {public_id}")
                    checked_count += 1
                    continue
                
                # Find the latest message that's NOT from us
                latest_reply = None
                for msg in reversed(messages):  # Start from most recent
                    sender = msg.get('sender', '').lower()
                    # Skip if it's from us (contains "you" or our name)
                    if 'you' not in sender and sender != full_name.lower():
                        continue
                    # This is from the candidate
                    if sender == full_name.lower() or sender != 'you':
                        latest_reply = msg
                        break
                
                if latest_reply:
                    reply_text = latest_reply.get('text', '')
                    
                    # Check if this is a new reply (different from what we have)
                    if profile_row.last_received_message != reply_text:
                        logger.info(f"📩 NEW REPLY from {full_name}: {reply_text[:50]}...")
                        save_received_message(session, public_id, reply_text)
                        new_replies_count += 1
                    else:
                        logger.debug(f"No new reply from {public_id}")
                
                checked_count += 1
                session.wait(2, 4)  # Polite delay between checks
                
            except Exception as e:
                logger.error(f"Error checking {profile_row.public_identifier}: {e}")
                continue
        
        logger.info(f"✅ Checked {checked_count} profiles. Found {new_replies_count} new replies.")
        
    finally:
        db_session.close()
        db_wrapper.Session.remove()


def main():
    """
    Main entry point - checks all active accounts.
    """
    from linkedin.conf import list_active_accounts
    
    active_accounts = list_active_accounts()
    
    if not active_accounts:
        logger.warning("No active accounts found.")
        return
    
    logger.info(f"Starting reply checker for {len(active_accounts)} account(s)...")
    
    for handle in active_accounts:
        try:
            check_replies_for_account(handle)
        except Exception as e:
            logger.error(f"Failed to check replies for @{handle}: {e}")
            continue
    
    logger.info("🎉 Reply check complete!")


if __name__ == "__main__":
    main()
