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
    Ultra-erratic human delay with micro-jittering and multi-stage sleeps.
    - 'normal': Standard jitter with high precision (e.g., 35.234s)
    - 'burst': Quick clicks (e.g., 1.259s)
    - 'break': Long distraction pause (2 - 7 minutes)
    """
    # 1. Base Delay Calculation
    if mode == "break":
        base_delay = random.uniform(120.34, 420.89)
        logger.info(colored(f"☕ Taking an organic break for {base_delay/60:.2f} minutes...", "yellow"))
    elif mode == "burst":
        base_delay = random.gauss((min_val + max_val) / 2, (max_val - min_val) / 4)
        base_delay = max(min_val * 0.8, min(base_delay, max_val * 1.2))
    else:
        # Standard Profile pacing
        mu = (min_val + max_val) / 2
        sigma = (max_val - min_val) / 5 # Wider variance
        base_delay = random.gauss(mu, sigma)
        
        # Add "Organic Noise" (The 35.20 and 41.9 feel)
        noise = random.uniform(-2.5, 2.5) 
        base_delay = max(min_val * 0.9, min(base_delay + noise, max_val * 1.1))

    # 2. Multi-Stage Sleep (Simulate human eye-scanning)
    # Instead of one big sleep, we sleep in small random chunks
    remaining = base_delay
    logger.info(f"Stealth Pause: {base_delay:.3f}s ({mode})")
    
    while remaining > 0:
        chunk = random.uniform(0.5, 4.5) # Sleep in 0.5s to 4.5s chunks
        sleep_time = min(chunk, remaining)
        
        # Add micro-jitter (milliseconds)
        jitter = random.uniform(0.001, 0.099)
        time.sleep(max(0, sleep_time + jitter))
        remaining -= sleep_time
        
        # Occasionally simulate a "mouse wiggle" pause
        if mode != "burst" and random.random() < 0.1:
            time.sleep(random.uniform(0.1, 0.8))

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
        self.burst_limit = random.randint(5, 12) # Randomize when to take a big break
        
        # 🟢 Ultra-Safe Batch Tracking
        self.profiles_scraped_this_batch = 0
        self.current_batch_limit = random.randint(3, 4)
        
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

    def human_scroll(self):
        """
        Simulates natural human scrolling behavior to trigger LinkedIn's 
        visibility and activity tracking.
        """
        if not self.page:
            return
            
        logger.info(f"🛡️ Performing human-like scrolling for {self.handle}...")
        try:
            # 1. Random initial pause
            time.sleep(random.uniform(1, 3))
            
            # 2. Variable speed scroll down
            total_height = self.page.evaluate("document.body.scrollHeight")
            current_pos = 0
            while current_pos < total_height * 0.7: # Scroll ~70% down
                step = random.randint(200, 600)
                current_pos += step
                self.page.evaluate(f"window.scrollTo({{top: {current_pos}, behavior: 'smooth'}})")
                time.sleep(random.uniform(0.5, 1.5))
                
                # Update total height in case of lazy loading
                total_height = self.page.evaluate("document.body.scrollHeight")

            # 3. Brief "reading" pause at the bottom
            time.sleep(random.uniform(2, 5))
            
            # 4. Quick scroll back to top
            self.page.evaluate("window.scrollTo({top: 0, behavior: 'smooth'})")
            time.sleep(random.uniform(1, 2))
            
        except Exception as e:
            logger.warning(f"Scroll simulation failed (non-critical): {e}")

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

    def reboot_browser(self):
        """
        Force-closes the current browser and re-initializes it.
        Useful after updating cookies/storage state.
        """
        logger.info(f"🔄 Rebooting browser engine for {self.handle}...")
        try:
            if self.context: self.context.close()
            if self.browser: self.browser.close()
            if self.playwright: self.playwright.stop()
        except: pass
        
        self.page = self.context = self.browser = self.playwright = None
        self.ensure_browser()

    def __del__(self):
        try:
            self.close()
        except:
            pass

    def __repr__(self) -> str:
        return f"<AccountSession {self.handle}>"
