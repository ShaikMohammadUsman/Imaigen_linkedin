# linkedin/navigation/login.py
import logging
from pathlib import Path

from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth
from termcolor import colored

from linkedin.conf import get_account_config
from linkedin.navigation.utils import goto_page
from linkedin.sessions.registry import get_session

logger = logging.getLogger(__name__)

LINKEDIN_LOGIN_URL = "https://www.linkedin.com/login"
LINKEDIN_FEED_URL = "https://www.linkedin.com/feed/"

SELECTORS = {
    "email": 'input#username',
    "password": 'input#password',
    "submit": 'button[type="submit"]',
    "google_btn_iframe": 'iframe[title="Sign in with Google Button"]',
    "google_email": 'input[type="email"]',
    "google_password": 'input[type="password"]',
}


def google_login_flow(session: "AccountSession", config: dict):
    """
    Handles the 'Sign in with Google' flow. 
    Note: This often triggers Google's bot detection or MFA.
    """
    page = session.page
    logger.info(colored("🚀 Initiating Google SSO login sequence...", "yellow", attrs=["bold"]))
    
    try:
        # 1. Click the Google Button (in iframe)
        # Wait for iframe to be ready
        page.wait_for_selector(SELECTORS["google_btn_iframe"], timeout=10000)
        google_iframe = page.frame_locator(SELECTORS["google_btn_iframe"])
        google_btn = google_iframe.locator('div[role="button"]').first
        
        # LinkedIn often uses a popup for Google login
        try:
            with page.context.expect_page(timeout=10000) as popup_info:
                google_btn.click()
            google_page = popup_info.value
        except:
             logger.info("Google login didn't open a popup. Checking for redirect...")
             google_page = page # Maybe it redirected the main page
        
        google_page.wait_for_load_state("networkidle")
        
        logger.info("Google login page active. Entering credentials...")
        
        # 2. Enter Google Email
        email_field = google_page.locator(SELECTORS["google_email"])
        if email_field.is_visible():
            email_field.fill(config["username"])
            google_page.keyboard.press("Enter")
        
        # Wait for password field
        google_page.wait_for_selector(SELECTORS["google_password"], timeout=15000)
        google_page.locator(SELECTORS["google_password"]).fill(config["password"])
        google_page.keyboard.press("Enter")
        
        # 3. Wait for popup to close and redirect back to LinkedIn
        logger.info("Submitted Google credentials. Waiting for redirect...")
        
        # LinkedIn should now redirect the main page to the feed
        page.wait_for_url(lambda l: "/feed" in l, timeout=60000)
        return True
        
    except Exception as e:
        logger.error(f"❌ Google Login Flow failed: {e}")
        logger.info(colored("⚠️ PLEASE USE VNC (localhost:5900) to complete the login manually if needed.", "red", attrs=["bold"]))
        # Give user time to intervene via VNC
        try:
            page.wait_for_url(lambda l: "/feed" in l, timeout=120000)
            return True
        except:
            return False


def playwright_login(session: "AccountSession"):
    page = session.page
    config = get_account_config(session.handle)
    # Default to google priority as requested
    login_method = config.get("login_method", "google").lower()
    
    logger.info(colored("Fresh login sequence starting", "cyan") + f" for @{session.handle} (Priority Method: {login_method})")

    goto_page(
        session,
        action=lambda: page.goto(LINKEDIN_LOGIN_URL),
        expected_url_pattern="/login",
        error_message="Failed to load login page",
        to_scrape=False
    )

    # 🟢 OPTION 1: Try Google Login First (Default Priority)
    if login_method == "google":
        logger.info("Attempting Google SSO login as first priority...")
        if google_login_flow(session, config):
            return
        logger.warning("Google SSO failed or timed out. Falling back to direct LinkedIn login...")

    # 🟢 OPTION 2: Direct LinkedIn Login (Fallback)
    logger.info("Proceeding with direct LinkedIn login credentials...")
    import random
    try:
        # Check if username field is visible
        if not page.locator(SELECTORS["email"]).is_visible():
             # If we are here and method was 'linkedin', try google as last resort
             if login_method != "google":
                 logger.info("Direct fields missing. Trying Google as fallback...")
                 google_login_flow(session, config)
             return

        # Human-like typing with randomized speed per character
        username = config["username"]
        for char in username:
            page.locator(SELECTORS["email"]).type(char, delay=random.randint(50, 140))
        
        session.wait(to_scrape=False)
        
        password = config["password"]
        for char in password:
            page.locator(SELECTORS["password"]).type(char, delay=random.randint(60, 160))
        
        session.wait(to_scrape=False)

        # Randomized mouse hover before click
        submit_btn = page.locator(SELECTORS["submit"])
        submit_btn.hover()
        time.sleep(random.uniform(0.5, 1.2))
        submit_btn.click()
        
        try:
            # Short wait for immediate success
            page.wait_for_url(lambda l: "/feed" in l, timeout=15000)
            logger.info(colored("Direct Login Successful!", "green"))
        except:
            # If still on login page, wait for manual override (VNC) or slow redirect
            logger.info("Waiting for manual intervention (MFA/ID Verification) via VNC...")
            page.wait_for_url(lambda l: "/feed" in l, timeout=60000)

    except Exception as e:
        logger.debug(f"Direct login encounter a problem: {e}")
        # One last desperate attempt at Google if we see the button
        if page.locator(SELECTORS["google_btn_iframe"]).is_visible():
            google_login_flow(session, config)
        else:
            raise e


def build_playwright(storage_state=None):
    import os
    is_headless = os.getenv("HEADLESS", "false").lower() == "true"
    
    logger.debug(f"Launching Playwright (Headless: {is_headless})")
    playwright = sync_playwright().start()
    
    browser = playwright.chromium.launch(
        headless=is_headless, 
        slow_mo=200 if not is_headless else None,
        args=[
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu"
        ] if is_headless else []
    )
    context = browser.new_context(storage_state=storage_state)
    from playwright_stealth import Stealth
    Stealth().apply_stealth_sync(context)
    page = context.new_page()
    return page, context, browser, playwright


def init_playwright_session(session: "AccountSession", handle: str):
    logger.info(colored("Configuring browser", "cyan", attrs=["bold"]) + f" for @{handle}")
    config = get_account_config(handle)
    state_file = Path(config["cookie_file"])

    storage_state = str(state_file) if state_file.exists() else None
    if storage_state:
        logger.info("Devouring saved cookies → %s", state_file)

    session.page, session.context, session.browser, session.playwright = build_playwright(storage_state=storage_state)

    if not storage_state:
        playwright_login(session)
        state_file.parent.mkdir(parents=True, exist_ok=True)
        session.context.storage_state(path=str(state_file))
        logger.info(colored("Login successful – session saved", "green", attrs=["bold"]) + f" → {state_file}")
    else:
        goto_page(
            session,
            action=lambda: session.page.goto(LINKEDIN_FEED_URL),
            expected_url_pattern="/feed",
            timeout=30_000,
            error_message="Saved session invalid",
            to_scrape=False
        )

    session.page.wait_for_load_state("load")
    logger.info(colored("Browser awake and fully authenticated!", "green", attrs=["bold"]))


if __name__ == "__main__":
    import sys



    logging.getLogger().handlers.clear()
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s │ %(levelname)-8s │ %(message)s',
        datefmt="%H:%M:%S",
    )

    if len(sys.argv) != 2:
        print("Usage: python -m linkedin.navigation.login <handle>")
        sys.exit(1)

    handle = sys.argv[1]

    session = get_session(
        handle=handle,
    )

    session.ensure_browser()

    # init_playwright_session(session=session, handle=handle) # REDUNDANT: ensure_browser() already calls this!
    print("Logged in! Close browser manually.")
    session.page.pause()
