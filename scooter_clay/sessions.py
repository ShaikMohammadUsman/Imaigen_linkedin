# scooter_clay/sessions.py
import logging
import time
import random
import os
from pathlib import Path
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth
from termcolor import colored

logger = logging.getLogger("ClaySession")

class ClaySession:
    def __init__(self, handle, config):
        self.handle = handle
        self.config = config
        self.browser = None
        self.context = None
        self.page = None
        self.playwright = None

    def init_browser(self, headless=False):
        self.playwright = sync_playwright().start()
        
        chrome_path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        
        clean_handle = self.handle.replace("@", "_").replace(".", "_")
        profile_dir = Path(f"assets/profiles/clay/trusted_{clean_handle}")
        profile_dir.mkdir(parents=True, exist_ok=True)
        
        # Cleanup lock files
        if profile_dir.exists():
            for filename in os.listdir(profile_dir):
                if filename.startswith("Singleton"):
                    try:
                        file_path = profile_dir / filename
                        if file_path.is_symlink():
                            os.unlink(file_path)
                        else:
                            os.remove(file_path)
                        logger.info(f"🧹 Force-cleared ghost lock: {filename}")
                    except: pass
        
        logger.info(colored(f"🚀 Attempting to launch Clay Browser (Handle: {self.handle})...", "cyan"))
        
        launch_args = {
            "user_data_dir": str(profile_dir),
            "headless": headless,
            "viewport": {'width': 1440, 'height': 900},
            "executable_path": chrome_path,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
                "--no-sandbox"
            ],
            "ignore_default_args": ["--enable-automation"]
        }
        
        try:
            self.context = self.playwright.chromium.launch_persistent_context(**launch_args)
            logger.info(colored(f"✅ Success! Connected to Chrome 🌐", "green"))
        except Exception as e:
            logger.error(colored(f"❌ Failed to launch Chrome: {e}", "red"))
            raise e
        
        # Deep Stealth Injection
        self.context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        """)
        
        Stealth().apply_stealth_sync(self.context)
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()

        # Check Authentication
        logger.info("Checking Clay session state...")
        try:
            self.page.goto("https://app.clay.com/workspaces", wait_until="domcontentloaded", timeout=60000)
            # Wait for either the dashboard to load OR a login element
            login_selector = "text='Welcome back', text='Sign in', text='session has expired', button:has-text('Google')"
            dashboard_selector = "text='Workspaces', [data-row-index], .clay-table"
            
            logger.info("🔭 Waiting for session or login screen...")
            self.page.wait_for_selector(f"{login_selector}, {dashboard_selector}", timeout=30000)
        except Exception as e:
            logger.warning(f"⚠️ Page load check timeout: {e}")

        current_url = self.page.url
        logger.info(f"Current Clay URL: {current_url}")
        
        # Detect login state by looking for specific text on the page
        is_login = any(x in current_url for x in ["auth", "sign-in", "login"]) or \
                   self.page.locator("text='Welcome back'").count() > 0 or \
                   self.page.locator("text='session has expired'").count() > 0 or \
                   self.page.locator("button:has-text('Google')").count() > 0
                   
        if is_login:
            logger.info(colored("🔑 LOGIN DETECTED: Opening manual entry mode...", "yellow", attrs=["bold"]))
            
            # Auto-click Google button if found
            google_btn = self.page.locator('button:has-text("Google"), [data-testid="google-login-button"]').first
            if google_btn.count() > 0:
                try: 
                    logger.info("Clicking Google SSO button...")
                    google_btn.click()
                except: pass

            logger.info(colored("⏳ Please login manually in the browser window (Google SSO).", "magenta", attrs=["bold"]))
            
            # Wait for user to reach ANY workspace URL or table URL
            try:
                self.page.wait_for_url("**/workspaces/**", timeout=300000) # 5 minutes
                logger.info(colored("✅ Session found! Letting it settle...", "green", attrs=["bold"]))
                time.sleep(10)
            except:
                raise Exception("Authentication Failed. Could not detect Clay Session.")
        else:
            logger.info("✅ No login required. Proceeding...")
        
        logger.info(colored("✅ Clay Session Verified & Ready.", "green", attrs=["bold"]))

    def close(self):
        if self.context: self.context.close()
        if self.playwright: self.playwright.stop()

class ClaySessionManager:
    def __init__(self):
        self.session = None

    def get_session(self, email):
        self.session = ClaySession(handle=email, config={})
        self.session.init_browser(headless=False)
        return self.session

    def close(self):
        if self.session:
            self.session.close()
