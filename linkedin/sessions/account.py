# linkedin/sessions/account.py
from __future__ import annotations

import logging
import random
import time

from linkedin.actions.profile import PlaywrightLinkedinAPI
from linkedin.conf import get_account_config, MIN_DELAY, MAX_DELAY, OPPORTUNISTIC_SCRAPING, MIN_UI_DELAY, MAX_UI_DELAY
from linkedin.navigation.login import init_playwright_session
from linkedin.navigation.throttle import determine_batch_size

logger = logging.getLogger(__name__)

MIN_API_DELAY = 0.250
MAX_API_DELAY = 0.500

def human_delay(min_val, max_val, mode="normal"):
    """
    Advanced human-like delay with Gaussian distribution.
    - 'normal': Standard jitter
    - 'burst': Quick succession interactions
    - 'break': Long distraction pause
    """
    if mode == "burst":
        # Quick actions (e.g. clicking through steps)
        delay = random.gauss((min_val + max_val) / 2, (max_val - min_val) / 4)
        delay = max(min_val, min(delay, max_val))
    elif mode == "break":
        # Coffee break / Reading (2 - 7 minutes)
        delay = random.uniform(120, 420)
        logger.info(colored(f"☕ Taking a human 'reading break' for {delay/60:.1f} minutes...", "yellow"))
    else:
        # Standard Profile-to-Profile or UI pacing
        # Use Gaussian centered between min/max
        mu = (min_val + max_val) / 2
        sigma = (max_val - min_val) / 6
        delay = random.gauss(mu, sigma)
        
        # Clamp to bounds but allow occasional outliers
        delay = max(min_val * 0.7, min(delay, max_val * 1.3))
    
    logger.debug(f"Stealth Pause: {delay:.2f}s ({mode})")
    time.sleep(delay)

from termcolor import colored


class AccountSession:
    def __init__(self, handle: str):
        from linkedin.db.engine import Database
        self.handle = handle
        self.config = get_account_config(handle)
        self.db = Database.from_handle(handle)  # Sync DB session wrapper
        
        # Browser state
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        
        # Determine strictness from config?
        # For now defaults
        self.headless = False # self.config.get("headless", True) # Default to visible for debugging
        self.actions_count = 0 
        self.burst_limit = random.randint(5, 10) # Randomize when to take a big break
        
    @property
    def db_session(self):
        return self.db.get_session()

    def ensure_browser(self):
        if self.page:
            return
        
        logger.info(f"Initializing browser for {self.handle}...")
        try:
            # Init session (modifies self in-place)
            init_playwright_session(self, self.handle)
            logger.info("Browser initialized successfully.")
        except Exception as e:
            logger.error(f"Failed to init browser: {e}")
            raise e

    def wait(self, min_delay=None, max_delay=None, to_scrape=OPPORTUNISTIC_SCRAPING, long_pause=False):
        """
        Smart Wait:
        - Default: Short UI delay for natural interactions.
        - long_pause=True: Long delay for pacing between profiles.
        """
        self.actions_count += 1
        
        # Determine Mode
        mode = "normal"
        if long_pause:
            # Check if it's time for a "Coffee Break"
            if self.actions_count >= self.burst_limit:
                mode = "break"
                self.actions_count = 0
                self.burst_limit = random.randint(5, 12) # Reset next break
            
            lower = min_delay or MIN_DELAY
            upper = max_delay or MAX_DELAY
        else:
            # UI interactions are faster "bursts"
            mode = "burst"
            lower = min_delay or MIN_UI_DELAY
            upper = max_delay or MAX_UI_DELAY

        if not to_scrape:
            human_delay(lower, upper, mode=mode)
            # Safe check for page state
            try:
                self.page.wait_for_load_state("domcontentloaded", timeout=5000) 
            except:
                pass
            return
            
        # ... (rest of scraping logic unchanged)
        
        from linkedin.db.profiles import get_next_url_to_scrape

        logger.debug(f"Pausing: {upper}s")
        amount_to_scrape = determine_batch_size(self)

        urls = get_next_url_to_scrape(self, limit=amount_to_scrape)
        if not urls:
            human_delay(lower, upper)
            try:
                self.page.wait_for_load_state("domcontentloaded", timeout=5000)
            except:
                pass
            return

        from linkedin.db.profiles import save_scraped_profile
        min_api_delay = max(min_delay / len(urls), MIN_API_DELAY)
        max_api_delay = max(max_delay / len(urls), MAX_API_DELAY)
        api = PlaywrightLinkedinAPI(session=self)

        for url in urls:
            human_delay(min_api_delay, max_api_delay, mode="burst")
            profile, data = api.get_profile(profile_url=url)
            save_scraped_profile(self, url, profile, data)
            logger.debug(f"Auto-scraped → {profile.get('full_name')} – {url}") if profile else None

    def close(self):
        if self.context:
            try:
                self.context.close()
                if self.browser:
                    self.browser.close()
                if self.playwright:
                    self.playwright.stop()
                logger.info("Browser closed gracefully (%s)", self.handle)
            except Exception as e:
                logger.debug("Error closing browser: %s", e)
            finally:
                self.page = self.context = self.browser = self.playwright = None

        self.db.close()
        logger.info("Account session closed → %s", self.handle)

    def __del__(self):
        try:
            self.close()
        except:
            pass

    def __repr__(self) -> str:
        return f"<AccountSession {self.handle}>"
