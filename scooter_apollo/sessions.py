# scooter_apollo/sessions.py
import logging
import time
import random
from pathlib import Path
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth
from termcolor import colored

logger = logging.getLogger("ApolloSession")

class ApolloSession:
    def __init__(self, handle, config):
        self.handle = handle
        self.config = config
        self.browser = None
        self.context = None
        self.page = None
        self.playwright = None
        self.auth_file = Path(f"assets/auth_{handle}.json")

    def init_browser(self, headless=False):
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(headless=headless)
        
        # Load storage state if exists
        storage_state = str(self.auth_file) if self.auth_file.exists() else None
        self.context = self.browser.new_context(storage_state=storage_state)
        
        # Apply Stealth
        Stealth().apply_stealth_sync(self.context)
        self.page = self.context.new_page()

        if not storage_state:
            self.login()
            self.context.storage_state(path=str(self.auth_file))
        else:
            self.page.goto("https://app.apollo.io/#/home")
            # Basic check if logged in, otherwise re-login
            if "login" in self.page.url:
                self.login()
                self.context.storage_state(path=str(self.auth_file))

    def login(self):
        page = self.page
        logger.info(colored(f"🚀 Starting Apollo login for {self.handle}", "cyan"))
        page.goto("https://app.apollo.io/#/login")

        # Priority: Google Login
        if self.config.get("login_method") == "google":
            self.google_sso()
        else:
            self.direct_login()

    def google_sso(self):
        page = self.page
        logger.info("Initiating Google SSO for Apollo...")
        # Apollo's Google button is usually a direct link or a popup
        try:
            with page.context.expect_page() as popup_info:
                page.click('a[href*="google"]') # Common selector for Apollo Google login
            
            google_page = popup_info.value
            google_page.fill('input[type="email"]', self.config["username"])
            google_page.keyboard.press("Enter")
            time.sleep(2)
            google_page.fill('input[type="password"]', self.config["password"])
            google_page.keyboard.press("Enter")
            
            # Wait for return to Apollo
            page.wait_for_url("**/#/home", timeout=60000)
        except Exception as e:
            logger.error(f"Google SSO Failed: {e}. Check VNC for MFA.")

    def direct_login(self):
        page = self.page
        page.fill('input[name="email"]', self.config["username"])
        page.fill('input[name="password"]', self.config["password"])
        page.click('button[type="submit"]')
        page.wait_for_url("**/#/home", timeout=30000)

    def close(self):
        if self.browser: self.browser.close()
        if self.playwright: self.playwright.stop()
