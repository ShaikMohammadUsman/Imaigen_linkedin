# campaigns/connect_follow_up.py
import logging
import random

from termcolor import colored

from linkedin.actions.connection_status import get_connection_status
from linkedin.db.profiles import set_profile_state, get_profile, save_scraped_profile
from linkedin.navigation.enums import MessageStatus
from linkedin.navigation.enums import ProfileState
from linkedin.navigation.exceptions import TerminalStateError, SkipProfile, ReachedConnectionLimit, AuthenticationError, DetectionError
from linkedin.navigation.utils import save_page
from linkedin.notifications import send_alert
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

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
        profile_obj: dict = None,
):
    from linkedin.actions.connect import send_connection_request
    from linkedin.actions.message import send_follow_up_message
    from linkedin.actions.profile import scrape_profile

    url = simple_profile['url']
    public_identifier = simple_profile['public_identifier']
    profile_row = get_profile(session, public_identifier)

    if profile_row:
        current_state = ProfileState(profile_row.state)

        # 🩹 Job ID Healing: If CSV has a job_id (or we can extract it from the app_link), 
        # we treat it as a 'Fresh Outreach' if it differs from the DB.
        incoming_job_id = simple_profile.get('job_id')
        if not incoming_job_id or incoming_job_id == "":
             app_link = simple_profile.get('app_link', "")
             if "/careers/" in app_link:
                  incoming_job_id = app_link.split("/careers/")[-1].strip("/")
                  logger.info(f"🎯 Recovered missing Job ID from URL: {incoming_job_id}")

        # Use the profile object's job_id if we already updated it in this loop to avoid re-resetting
        profile = profile_obj or profile_row.profile or simple_profile.copy()
        current_job_id = profile.get('job_id') if profile else (profile_row.last_job_id if profile_row else None)

        if incoming_job_id and str(current_job_id) != str(incoming_job_id):
            # If the candidate exists but was handled for a DIFFERENT job, we 're-open' them
            if profile_row and current_state in [ProfileState.COMPLETED, ProfileState.PENDING, ProfileState.CONNECTED]:
                logger.info(f"🔄 Candidate {public_identifier} detected for a NEW Job ID ({incoming_job_id}). Re-opening for fresh outreach!")
                current_state = ProfileState.ENRICHED 
                
                # 🛡️ Mandatory: Clear old note from the previous job so AI re-generates it fresh.
                profile.pop('note', None)
                profile.pop('note_sent', None)
        
        # Ensure the memory object has the id
        if profile and incoming_job_id:
             profile['job_id'] = incoming_job_id
        
        # --- Metadata Override (Priority: Newest CSV Data) ---
        # Crucial fix: ALWAYS use the most recent Role, Company, etc. from the CSV to avoid reusing data from an old job.
        for key in ["role_name", "company_name", "app_link", "location", "compensation"]:
            if key in simple_profile and simple_profile[key]:
                profile[key] = simple_profile[key]
        
        # Ensure the job_id we're using is the healed one
        profile['job_id'] = incoming_job_id

        # --- AI Safety Check: Mismatch Awareness ---
        candidate_headline = profile.get("headline", "").lower()
        target_job_role = simple_profile.get("role_name", "").lower()
        
        tech_keywords = ["developer", "engineer", "tech", "backend", "frontend", "data", "scientist"]
        sales_keywords = ["sales", "account", "growth", "business development", "marketing"]
        
        is_tech = any(kw in candidate_headline for kw in tech_keywords)
        is_sales = any(kw in target_job_role for kw in sales_keywords)
        
        if is_tech and is_sales:
             logger.warning(colored(f"⚠️ MISMATCH: Sending a SALES job to a TECH guy ({public_identifier}).", "red", attrs=["bold"]))
        elif any(kw in candidate_headline for kw in sales_keywords) and any(kw in target_job_role for kw in tech_keywords):
             logger.warning(colored(f"⚠️ MISMATCH: Sending a TECH job to a SALES guy ({public_identifier}).", "red", attrs=["bold"]))

        logger.debug(f"Actual state: {public_identifier}  {current_state}")
    else:
        current_state = ProfileState.DISCOVERED
        profile = simple_profile

    new_state = None
    match current_state:
        case ProfileState.COMPLETED | ProfileState.FAILED:
            return None, current_state

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
                    return None, new_state

        case ProfileState.ENRICHED:
            if enrich_only:
                logger.info(f"Skipping {public_identifier} (Already Enriched & Enrich Mode ON)")
                return None, current_state
                
            if not perform_connections:
                return None, current_state
            
            # --- Personalize Note if missing ---
            if not profile.get('note'):
                from linkedin.templates.renderer import render_template
                try:
                    template_file = session.config.get("connection_template")
                    template_type = session.config.get("connection_template_type", "ai_prompt")
                    if template_file:
                        logger.info(f"🎨 Generating AI connection note for {public_identifier}...")
                        note = render_template(session, template_file, template_type, profile, include_link=True)
                        # Ensure it's not too long for LinkedIn tier limit (200 chars)
                        if len(note) > 195:
                            note = note[:192] + "..."
                        profile['note'] = note
                except Exception as e:
                    logger.error(f"Failed to generate AI connection note for {public_identifier}: {e}")
            
            new_state = send_connection_request(handle=handle, profile=profile)
            profile = profile if new_state == ProfileState.CONNECTED else None
        case ProfileState.PENDING:
            if enrich_only: return None, current_state
            new_state = get_connection_status(session, profile)
            profile = profile if new_state == ProfileState.CONNECTED else None
            
            # 🧬 If they just accepted, sync their thread to see if they sent a "Hello" or "Accepted" message
            if new_state == ProfileState.CONNECTED and profile:
                from linkedin.db.profiles import save_conversation_history
                from linkedin.actions.chat import fetch_latest_messages
                try:
                    logger.info(f"🧬 New Connection! Syncing initial conversational memory for {public_identifier}...")
                    chat_msgs = fetch_latest_messages(handle, profile, limit=5)
                    if chat_msgs:
                        save_conversation_history(session, public_identifier, chat_msgs)
                except Exception as e:
                    logger.warning(f"⚠️ Memory Sync failed (skipping): {e}")

            session.wait(long_pause=True)  # <-- Pacing delay after checking status
        case ProfileState.CONNECTED:
            if enrich_only: return None, current_state
            from linkedin.db.profiles import save_message_sent, save_conversation_history
            from linkedin.actions.chat import fetch_latest_messages
            
            # 🧬 MEMORY BRIDGE: Sync conversation history before follow-up
            try:
                logger.info(f"🧬 Syncing conversational memory for {public_identifier}...")
                chat_msgs = fetch_latest_messages(handle, profile, limit=7)
                if chat_msgs:
                    save_conversation_history(session, public_identifier, chat_msgs)
            except Exception as e:
                logger.warning(f"⚠️ Memory Sync failed for {public_identifier} (Skipping): {e}")

            status, msg_text = send_follow_up_message(
                handle=handle,
                profile=profile,
            )
            new_state = message_status_to_state.get(status, ProfileState.CONNECTED)
            profile = profile if status == MessageStatus.SENT else None
            
            if status == MessageStatus.SENT:
                save_message_sent(session, public_identifier, msg_text, job_id=simple_profile.get('job_id'))
                session.wait(long_pause=True)  # <-- IMPORTANT: Long pause after sending message

        case _:
            raise TerminalStateError(f"Profile {public_identifier} is {current_state}")

    set_profile_state(session, public_identifier, new_state.value)
    
    # 🩹 Job ID Healing check for the return
    if incoming_job_id:
         profile['job_id'] = incoming_job_id if profile else None

    return profile, new_state


def process_profiles(handle, session, profiles: list[dict], enrich_only: bool = False, limit: int = 20):
    from linkedin.usage_tracker import UsageTracker
    from linkedin.conf import ASSETS_DIR
    from linkedin.db.jobs import create_job, update_job_progress, end_job
    
    tracker = UsageTracker(ASSETS_DIR)
    tracker.record_session(handle)
    
    # Create the job in SQL
    job_type = "enrich_only" if enrich_only else "campaign"
    job_id = create_job(session.db_session, handle, job_type, limit)
    
    perform_connections = True
    MAX_ACTIONS = limit
    actions_count = 0 
    
    error_msg = None
    stop_status = "completed"

    for simple_profile in profiles:
        public_identifier = simple_profile.get("public_identifier", "Unknown")
        
        # 🔬 INSPECTING CSV DATA
        incoming_job_id = simple_profile.get('job_id')
        role_name = simple_profile.get('role_name')
        logger.info(colored(f"🔬 INSPECTING CSV DATA: ID={incoming_job_id}, Role={role_name} for {public_identifier}", "cyan"))

        # Check overall daily & monthly safety (persisted)
        if not tracker.check_safety(handle, "enrich_profiles", "enrich_profiles"):
            logger.warning(colored(f"🛑 Enrichment limit reached for {handle}. Stopping.", "red", attrs=["bold"]))
            break

        # 🟢 Batch Pause (3-7 mins) after every 3-4 profiles
        if session.profiles_scraped_this_batch >= session.current_batch_limit:
            msg = f"☕ Batch complete. Taking a human pause of several minutes..."
            logger.info(colored(msg, "yellow"))
            from linkedin.sessions.account import human_delay
            human_delay(180, 420, mode="break")
            session.profiles_scraped_this_batch = 0
            session.current_batch_limit = random.randint(3, 4)

        if actions_count >= limit:
            logger.info(colored(f"🏁 Reached requested limit of {limit} profiles. Done for this session!", "green", attrs=["bold"]))
            break
            
        logger.info(colored(f"🔍 [ITERATION START] Beginning processing loop for: {public_identifier}", "magenta"))

        continue_same_profile = True
        # 🚀 Two-Step Flow: We allow up to 2 state transitions per profile (to go from Scraped -> Connected -> Messaged)
        # while still avoiding the 'Never-Ending Loops' by using a max-steps counter.
        max_steps = 2
        step_count = 0
        profile_in_turn = None
        
        try:
            while step_count < max_steps:
                 step_count += 1
                 profile_in_turn, new_state = process_profile_row(
                     handle=handle,
                     session=session,
                     simple_profile=simple_profile,
                     perform_connections=perform_connections,
                     enrich_only=enrich_only,
                     profile_obj=profile_in_turn
                 )
                 
                 # 🚀 Logic Update: We only 'charge' an action if we actually SENT something.
                 # If we just found out they are 'Already Connected', we haven't spent an action yet!
                 
                 # We increment if:
                 # 1. We are in Enrich-Only mode and we just finished Enriching.
                 # 2. We just sent a Connection Invitation (new_state becomes PENDING).
                 # 3. We just sent a Message (this happens when process_profile_row returns profile=None after SUCCESS).
                 
                 invitation_sent = (new_state == ProfileState.PENDING)
                 message_sent = (profile_in_turn is None and new_state == ProfileState.CONNECTED and not enrich_only)
                 enrichment_done = (new_state == ProfileState.ENRICHED and enrich_only)
                 
                 should_increment = invitation_sent or message_sent or enrichment_done
                 
                 if should_increment:
                      actions_count += 1
                      session.profiles_scraped_this_batch += 1
                      tracker.increment(handle, "enrich_profiles")
                      tracker.record_health_event(handle, "success")
                      
                      update_job_progress(session.db_session, job_id, actions_count)
                      
                      # 🚨 FORENSIC TRACKING: Stamp the job ID onto the candidate
                      try:
                          from linkedin.db.models import Profile
                          db_prof = session.db_session.query(Profile).filter_by(public_identifier=simple_profile.get("public_identifier")).first()
                          if db_prof:
                              incoming_id = simple_profile.get('job_id')
                              if not incoming_id or incoming_id == "":
                                   app_link = simple_profile.get('app_link', "")
                                   if "/careers/" in app_link:
                                        incoming_id = app_link.split("/careers/")[-1].strip("/")
                              
                              db_prof.last_job_id = str(incoming_id)
                              session.db_session.commit()
                      except Exception as e:
                          logger.warning(colored(f"🛡️ DB Safety: Job ID could not be saved to DB (likely type mismatch), but outreach will continue! Error: {e}", "yellow"))
                          session.db_session.rollback()
                      
                      logger.info(f"Action count: {actions_count}/{limit}")

                 # If we actually finished the turn for this person, break the while loop and move to next CSV row.
                 if profile_in_turn is None or should_increment:
                      break
        except SkipProfile as e:
            public_identifier = simple_profile["public_identifier"]
            logger.info(
                colored(f"Skipping profile: {public_identifier} reason: {e}", "red", attrs=["bold"])
            )
            save_page(session, simple_profile)
        except ReachedConnectionLimit as e:
            perform_connections = False
            public_identifier = simple_profile["public_identifier"]
            logger.info(
                colored(f"Skipping profile: {public_identifier} reason: {e}", "red", attrs=["bold"])
            )
            send_alert(f"Weekly Connection Limit Reached for @{handle}.", category="limit")
        except AuthenticationError as e:
            from linkedin.navigation.login import manual_login_checkpoint
            logger.warning(colored(f"🚨 AUTH FAILURE (401): {e}", "red", attrs=["bold"]))
            logger.info(colored("🛡️ TRIGGERING MANUAL LOGIN POPUP...", "cyan", attrs=["bold"]))
            
            # Pop the manual browser safely
            session.close_browser()
            if manual_login_checkpoint(handle):
                logger.info(colored("✅ Manual Login Successful! Rebooting bot...", "green", attrs=["bold"]))
                session.ensure_browser()
            else:
                logger.error(colored("❌ Manual Login Failed. Stopping campaign.", "red", attrs=["bold"]))
                stop_status = "failed"
                error_msg = "Manual Login Failed"
                break
        except DetectionError as e:
            logger.error(colored(f"🛑 DETECTION ERROR: {e}. Stopping all operations.", "red", attrs=["bold"]))
            stop_status = "failed"
            error_msg = f"Detection Error: {e}"
            break
        except PlaywrightTimeoutError as e:
            logger.error(f"Timeout processing {simple_profile['public_identifier']}: {e}")
            tracker.record_health_event(handle, "timeout", details=str(e))
        except Exception as e:
            logger.error(f"Unexpected failure for {simple_profile['public_identifier']}: {e}", exc_info=True)
            tracker.record_health_event(handle, "unknown_failure", details=str(e))

    try:
        end_job(session.db_session, job_id, stop_status, error_msg)
    except Exception as e:
        logger.debug(f"Failed to end job record cleanly: {e}")
        session.db_session.rollback()
