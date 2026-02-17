# campaigns/connect_follow_up.py
import logging

from termcolor import colored

from linkedin.actions.connection_status import get_connection_status
from linkedin.db.profiles import set_profile_state, get_profile, save_scraped_profile
from linkedin.navigation.enums import MessageStatus
from linkedin.navigation.enums import ProfileState
from linkedin.navigation.exceptions import TerminalStateError, SkipProfile, ReachedConnectionLimit, AuthenticationError, DetectionError
from linkedin.navigation.utils import save_page

logger = logging.getLogger(__name__)

message_status_to_state = {
    MessageStatus.SENT: ProfileState.COMPLETED,
    MessageStatus.SKIPPED: ProfileState.CONNECTED,
}


def process_profile_row(
        handle: str,
        session: "AccountSession",
        simple_profile: dict,
        perform_connections=True,
        enrich_only: bool = False,
):
    from linkedin.actions.connect import send_connection_request
    from linkedin.actions.message import send_follow_up_message
    from linkedin.actions.profile import scrape_profile

    url = simple_profile['url']
    public_identifier = simple_profile['public_identifier']
    profile_row = get_profile(session, public_identifier)

    if profile_row:
        current_state = ProfileState(profile_row.state)
        profile = profile_row.profile or simple_profile.copy()
        
        # Ensure metadata (Role, Company, Link, Location, Compensation) is available
        for key in ["role_name", "company_name", "app_link", "location", "compensation"]:
            if key not in profile and key in simple_profile:
                profile[key] = simple_profile[key]
                
    else:
        current_state = ProfileState.DISCOVERED
        profile = simple_profile

    logger.debug(f"Actual state: {public_identifier}  {current_state}")

    new_state = None
    match current_state:
        case ProfileState.COMPLETED | ProfileState.FAILED:
            return None

        case ProfileState.DISCOVERED:
            profile, data = scrape_profile(handle=handle, profile=profile)
            if profile is None:
                new_state = ProfileState.FAILED
            else:
                new_state = ProfileState.ENRICHED
                save_scraped_profile(session, url, profile, data)
                
                if enrich_only:
                    logger.info(f"✨ Enriched {public_identifier}. Stopping (Enrich Mode).")
                    set_profile_state(session, public_identifier, new_state.value)
                    return None

        case ProfileState.ENRICHED:
            if enrich_only:
                logger.info(f"Skipping {public_identifier} (Already Enriched & Enrich Mode ON)")
                return None
                
            if not perform_connections:
                return None
            new_state = send_connection_request(handle=handle, profile=profile)
            profile = None if new_state != ProfileState.CONNECTED else profile
        case ProfileState.PENDING:
            if enrich_only: return None
            new_state = get_connection_status(session, profile)
            profile = None if new_state != ProfileState.CONNECTED else profile
            session.wait(long_pause=True)  # <-- Pacing delay after checking status
        case ProfileState.CONNECTED:
            if enrich_only: return None
            from linkedin.db.profiles import save_message_sent
            status, msg_text = send_follow_up_message(
                handle=handle,
                profile=profile,
            )
            new_state = message_status_to_state.get(status, ProfileState.CONNECTED)
            profile = None if status != MessageStatus.SENT else profile
            
            if status == MessageStatus.SENT:
                save_message_sent(session, public_identifier, msg_text)
                session.wait(long_pause=True)  # <-- IMPORTANT: Long pause after sending message

        case _:
            raise TerminalStateError(f"Profile {public_identifier} is {current_state}")

    set_profile_state(session, public_identifier, new_state.value)

    return profile


def process_profiles(handle, session, profiles: list[dict], enrich_only: bool = False, limit: int = 20):
    from linkedin.usage_tracker import UsageTracker
    from linkedin.conf import ASSETS_DIR
    
    tracker = UsageTracker(ASSETS_DIR)
    perform_connections = True
    MAX_ACTIONS = limit
    actions_count = 0 

    for simple_profile in profiles:
        # Check overall daily safety (persisted)
        if not tracker.check_enrich_safety(handle):
            logger.warning(colored(f"🛑 Persisted daily enrichment limit reached for {handle}. Stopping.", "red", attrs=["bold"]))
            break

        if actions_count >= MAX_ACTIONS:
            logger.info(colored(f"🛑 Session limit reached ({MAX_ACTIONS} actions). Stopping for now.", "red", attrs=["bold"]))
            break

        continue_same_profile = True
        while continue_same_profile:
            try:
                profile = process_profile_row(
                    handle=handle,
                    session=session,
                    simple_profile=simple_profile,
                    perform_connections=perform_connections,
                    enrich_only=enrich_only,
                )
                
                # If we processed a profile (scraped, invited, or messaged)
                if (profile is None and enrich_only) or profile:
                     actions_count += 1
                     tracker.increment(handle, "enrich_profiles")
                     logger.info(f"Action count: {actions_count}/{MAX_ACTIONS}")

                continue_same_profile = bool(profile)
            except SkipProfile as e:
                public_identifier = simple_profile["public_identifier"]
                logger.info(
                    colored(f"Skipping profile: {public_identifier} reason: {e}", "red", attrs=["bold"])
                )
                save_page(session, simple_profile)
                continue_same_profile = False
            except ReachedConnectionLimit as e:
                perform_connections = False
                public_identifier = simple_profile["public_identifier"]
                logger.info(
                    colored(f"Skipping profile: {public_identifier} reason: {e}", "red", attrs=["bold"])
                )
                continue_same_profile = False
            except (AuthenticationError, DetectionError) as e:
                logger.error(colored(f"🛑 CRITICAL ERROR: {e}. Stopping all operations.", "red", attrs=["bold"]))
                return # Exit the entire function
