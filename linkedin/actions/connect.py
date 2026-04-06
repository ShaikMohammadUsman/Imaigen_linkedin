# linkedin/actions/connect.py
import logging
from typing import Dict, Any
from termcolor import colored

from linkedin.navigation.enums import ProfileState
from linkedin.navigation.exceptions import SkipProfile, ReachedConnectionLimit
from linkedin.navigation.utils import get_top_card
from linkedin.sessions.registry import get_session

logger = logging.getLogger(__name__)


def send_connection_request(
        handle: str,
        profile: Dict[str, Any],
) -> ProfileState:
    """
    Sends a LinkedIn connection request. 
    If profile['note'] exists, it sends with a personalized note.
    Otherwise, it sends a direct invitation without a note.
    """
    from linkedin.actions.connection_status import get_connection_status

    session = get_session(
        handle=handle,
    )

    public_identifier = profile.get('public_identifier')
    note = profile.get('note')

    logger.debug("Checking current connection status...")
    connection_status = get_connection_status(session, profile)
    logger.info("Current status → %s", connection_status.value)

    skip_reasons = {
        ProfileState.CONNECTED: "Already connected",
        ProfileState.PENDING: "Invitation already pending",
    }

    if connection_status in skip_reasons:
        logger.info("Skipping %s – %s", public_identifier, skip_reasons[connection_status])
        return connection_status

    if note:
        logger.info(f"Sending connection request WITH NOTE to {public_identifier}")
        success = _perform_send_invitation_with_note(session, note)
    else:
        logger.info(f"Sending connection request WITHOUT NOTE to {public_identifier}")
        # Send invitation WITHOUT note
        s1 = _connect_direct(session)
        s2 = s1 or _connect_via_more(session)
        s3 = s2 and _click_without_note(session)
        success = s3

    if success:
        _check_weekly_invitation_limit(session)
        status = ProfileState.PENDING
    else:
        status = ProfileState.ENRICHED

    logger.info(f"Connection request {status.value} → {public_identifier}")
    return status


def _check_weekly_invitation_limit(session):
    weekly_invitation_limit = session.page.locator('div[class*="ip-fuse-limit-alert__warning"]')
    if weekly_invitation_limit.count() != 0:
        raise ReachedConnectionLimit("Weekly connection limit pop up appeared")

    return True


def _connect_direct(session):
    session.wait()
    top_card = get_top_card(session)
    # Broadly search for any Connect button
    direct = session.page.locator(
        'button[aria-label*="Invite"]:visible, '
        'button[aria-label*="to connect"]:visible, '
        'button[aria-label^="Connect with"]:visible, '
        'button:text-is("Connect"):visible'
    ).first
    
    if direct.count() == 0:
        return False

    direct.click()
    logger.debug("Clicked direct 'Connect' button")

    error = session.page.locator('div[data-test-artdeco-toast-item-type="error"]')
    if error.count() != 0:
        raise SkipProfile(f"{error.inner_text().strip()}")

    return True


def _connect_via_more(session):
    session.wait()
    top_card = get_top_card(session)

    # Fallback: More → Connect
    more = top_card.locator(
        'button[id*="overflow"]:visible, '
        'button[aria-label*="More actions"]:visible'
    )
    if more.count() == 0:
        return False
    more.first.click()

    session.wait()

    connect_option = top_card.locator(
        'div[role="button"][aria-label^="Invite"][aria-label*=" to connect"]'
    )
    if connect_option.count() == 0:
        return False
    connect_option.first.click()
    logger.debug("Used 'More → Connect' flow")

    return True


def _click_without_note(session):
    """Click flow: sends connection request instantly without note."""
    session.wait()

    # Click "Send now" / "Send without a note"
    send_btn = session.page.locator(
        'button:has-text("Send now"), '
        'button[aria-label*="Send without"], '
        'button[aria-label*="Send invitation"]:not([aria-label*="note"])'
    )
    send_btn.first.click(force=True)
    session.wait()
    logger.debug("Connection request submitted (no note)")

    return True


# ===================================================================
# FUTURE: Send with personalized note (just uncomment when ready)
# ===================================================================
def _perform_send_invitation_with_note(session, message: str):
    """Full flow with custom note – ready to enable anytime."""
    session.wait()
    top_card = get_top_card(session)

    # 1. Broadly search for any button that looks like a Connect/Invite button on the whole page
    direct = session.page.locator(
        'button[aria-label*="Invite"]:visible, '
        'button[aria-label*="to connect"]:visible, '
        'button[aria-label^="Connect with"]:visible, '
        'button:text-is("Connect"):visible'
    ).first
    
    if direct.count() > 0:
        logger.debug("Found explicit Connect button. Clicking it.")
        direct.click()
    else:
        logger.debug("No direct Connect button found. Trying 'More' dropdown...")
        more = top_card.locator(
            'button[id*="overflow"]:visible, '
            'button[aria-label*="More actions"]:visible'
        )
        if more.count() == 0:
            logger.warning("Abort: Neither 'Connect' nor 'More' buttons were found on the profile.")
            return False
        more.first.click()
        session.wait()
        
        connect_option = top_card.locator('div[role="button"][aria-label^="Invite"][aria-label*=" to connect"]')
        if connect_option.count() == 0:
            logger.warning("Abort: 'More' was clicked, but there was no 'Connect' option inside the dropdown.")
            return False
        connect_option.first.click()

    session.wait()
    add_note_btn = session.page.locator('button:has-text("Add a note"), button[aria-label*="Add a note"]')
    if add_note_btn.count() == 0:
        logger.info(colored("⚠️ LinkedIn restricted custom notes for this account (Limit Reached or Privacy Settings).", "yellow", attrs=["bold"]))
        logger.info("Attempting a clean, raw connection request instead...")
        
        # Fallback 1: 'Send without a note' button
        send_without_note = session.page.locator('button:has-text("Send without a note")')
        if send_without_note.count() > 0:
            logger.info("Falling back to 'Send without a note'. Job pitch will wait until they accept.")
            send_without_note.first.click(force=True)
            session.wait()
            return True
            
        # Fallback 2: Direct 'Send' button (if it's the only one left on the modal)
        send_bare = session.page.locator('button[aria-label*="Send invitation"], button:has-text("Send"):visible')
        if send_bare.count() > 0:
            logger.info("Falling back to raw 'Send' button. Job pitch will wait until they accept.")
            send_bare.first.click(force=True)
            session.wait()
            return True

        logger.warning("Abort: The connection modal opened, but no 'Add a note' or 'Send' button was found.")
        return False
        
    add_note_btn.first.click()
    session.wait()

    textarea = session.page.locator('textarea#custom-message, textarea[name="message"]')
    textarea.first.fill(message)
    session.wait()
    logger.debug("Filled note (%d chars)", len(message))

    dialog = session.page.locator('div[role="dialog"]')
    
    send_btn = dialog.locator('button:has-text("Send"), button[aria-label*="Send invitation"]')
    if send_btn.count() > 0:
        if send_btn.first.is_disabled():
            logger.warning(colored(f"⚠️ Abort: AI pitch was {len(message)} chars, exceeding LinkedIn's limit! The Send button got disabled.", "red", attrs=["bold"]))
            logger.info("Dismissing modal. Please adjust your prompt to be even shorter or remove the URL.")
            close_btn = dialog.locator('button[aria-label="Dismiss"]')
            if close_btn.count() > 0:
                close_btn.first.click()
            return False
            
        send_btn.first.click()
        session.wait()
        
        # 🛡️ Post-click safety check: Did LinkedIn reject it?
        error_toast = session.page.locator('div.artdeco-toast-item--error')
        if error_toast.count() > 0:
            toast_text = error_toast.first.inner_text().strip()
            logger.warning(colored(f"❌ LinkedIn rejected the request! Reason: {toast_text}", "red", attrs=["bold"]))
            
            # Dismiss the modal so we can cleanly exit
            close_btn = dialog.locator('button[aria-label="Dismiss"]')
            if close_btn.count() > 0:
                close_btn.first.click()
            return False

        logger.debug("Connection request with note sent")
        return True
    
    logger.warning("Abort: Note was filled, but the final 'Send' button could not be found.")
    return False


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        print("Usage: python -m linkedin.actions.connect <handle>")
        sys.exit(1)

    handle = sys.argv[1]

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    public_identifier = "benjames01"
    test_profile = {
        "full_name": "Ben James",
        "url": f"https://www.linkedin.com/in/{public_identifier}/",
        "public_identifier": public_identifier,
    }

    print(f"Testing connection request as @{handle} )")
    status = send_connection_request(
        handle=handle,
        profile=test_profile,
    )

    print(f"Finished → Status: {status.value}")
