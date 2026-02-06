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

def human_delay(min_val, max_val):
    """
    Human-like delay with realistic decimal precision.
    Instead of 5.00s, generates values like 3.27s, 5.81s, 7.43s
    """
    # Add small random variance to min/max to avoid patterns
    min_variance = random.uniform(-0.3, 0.3)
    max_variance = random.uniform(-0.5, 0.5)
    
    adjusted_min = max(0.5, min_val + min_variance)
    adjusted_max = max_val + max_variance
    
    delay = random.uniform(adjusted_min, adjusted_max)
    logger.debug(f"Pause: {delay:.2f}s")
    time.sleep(delay)


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
        - Default: Short UI delay (1.5 - 4s) for natural interactions.
        - long_pause=True: Long delay (45 - 120s) for pacing between profiles.
        """
        # Determine strict bounds
        if long_pause:
            # Macro delay (between profiles)
            lower = min_delay or MIN_DELAY
            upper = max_delay or MAX_DELAY
        else:
            # Micro delay (UI interactions)
            lower = min_delay or MIN_UI_DELAY
            upper = max_delay or MAX_UI_DELAY

        if not to_scrape:
            human_delay(lower, upper)
            # Safe check for page state, but don't error if it's just a partial update
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
            human_delay(min_api_delay, max_api_delay)
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
