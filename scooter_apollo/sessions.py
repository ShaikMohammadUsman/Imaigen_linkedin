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
        
        # Paths for Mac
        chrome_path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        brave_path = "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"
        
        clean_handle = self.handle.replace("@", "_").replace(".", "_")
        profile_dir = Path(f"assets/profiles/apollo/trusted_{clean_handle}")
        profile_dir.mkdir(parents=True, exist_ok=True)
        
        # Cleanup any stuck lock files from previous crashes (SingletonLock is a symlink often)
        import os
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
        
        # Priority Order: 1. Chrome (Native), 2. Brave, 3. Bundled
        # Using Native Chrome is MUCH better for Google SSO persistence
        browser_to_try = [
            ("Chrome 🌐", chrome_path),
            ("Brave 🦁", brave_path), 
            ("Bundled Playwright Browser ⚙️", None)
        ]
        
        self.context = None
        for name, path in browser_to_try:
            try:
                # Close any existing playwright instance before retry
                logger.info(colored(f"🚀 Attempting to launch {name}...", "cyan"))
                
                launch_args = {
                    "user_data_dir": str(profile_dir),
                    "headless": headless,
                    "viewport": {'width': 1280, 'height': 800}
                }
                
                if path:
                    launch_args["executable_path"] = path
                    # These flags are CRITICAL for letting Google SSO iframe work inside Apollo
                    launch_args["args"] = [
                        "--disable-blink-features=AutomationControlled",
                        "--disable-features=IsolateOrigins,site-per-process",
                        "--no-sandbox"
                    ]
                    # CRITICAL: This removes the "Chrome is being controlled by automated software" header
                    launch_args["ignore_default_args"] = ["--enable-automation"]
                
                self.context = self.playwright.chromium.launch_persistent_context(**launch_args)
                
                if self.context: 
                    logger.info(colored(f"✅ Success! Connected to {name}", "green"))
                    break
            except Exception as e:
                logger.warning(colored(f"⚠️ {name} failed: {str(e).split('\\n')[0]}", "yellow"))
                time.sleep(3)
                continue
        
        if not self.context:
            raise Exception("Failed to launch any browser. Please ensure Chrome or Brave is closed.")
        
        # Deep Stealth Injection
        self.context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        """)
        
        Stealth().apply_stealth_sync(self.context)
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()

        # Check Authentication properly (with retry and longer timeout)
        logger.info("Checking session state...")
        for attempt in range(2):
            try:
                self.page.goto("https://app.apollo.io/#/home", wait_until="domcontentloaded", timeout=60000)
                time.sleep(5) 
                break
            except Exception as e:
                if attempt == 1: raise e
                logger.warning("⚠️ Session check timeout. Retrying...")
                time.sleep(5)
        
        # Strict Check: If we are on login, trigger the manual flow
        if "login" in self.page.url or ("home" not in self.page.url and "mailbox" not in self.page.url):
            logger.info(colored("🔑 Authentication Required. Please log in manually in the browser window.", "yellow", attrs=["bold"]))
            self.login()
            # Double check with a long timeout for the user
            try:
                # Accept home dashboard OR the mailbox limit page (harvester will bypass)
                self.page.wait_for_function(
                    "() => window.location.href.includes('#/home') || window.location.href.includes('mailbox') || window.location.href.includes('callback') || window.location.href.includes('onboarding')",
                    timeout=600000 # Increase to 10 minutes for slow manual input
                )
            except:
                raise Exception("Authentication Failed. Could not detect Apollo Session.")
        
        logger.info(colored("✅ Apollo Session Verified & Ready.", "green", attrs=["bold"]))

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
        logger.info(colored("🔑 GOOGLE LOGIN MODE: MANUAL", "magenta", attrs=["bold"]))
        
        # Check if we are already blocked by Google's 'Unsecure Browser'
        if "google.com" in page.url and page.locator('text="This browser or app may not be secure"').count() > 0:
             logger.error(colored("\n🛑 GOOGLE HAS DETECTED AUTOMATION! 🛑", "red", attrs=["bold"]))
             logger.info(colored("To bypass this, follow these 3 steps:", "white"))
             
             # Create the command for the user
             clean_handle = self.handle.replace("@", "_").replace(".", "_")
             profile_path = Path(f"assets/profiles/apollo/trusted_{clean_handle}").absolute()
             chrome_cmd = f'"{self.chrome_path}" --user-data-dir="{profile_path}"'
             
             print(colored("-" * 60, "yellow"))
             print(colored("1. CLOSE THIS SCRIPT (Ctrl+C)", "white"))
             print(colored("2. RUN THIS NATIVE COMMAND IN YOUR TERMINAL:", "cyan"))
             print(colored(f"\n   {chrome_cmd}\n", "green", attrs=["bold"]))
             print(colored("3. IN THE WINDOW THAT OPENS: Login to Apollo via Google manually.", "white"))
             print(colored("4. CLOSE CHROME AND RE-RUN THE BOT.", "white"))
             print(colored("-" * 60, "yellow"))
             
             input("\nPress Enter once you have closed the native window to try and continue, or Ctrl+C to stop...")

        logger.info(colored("⏳ Waiting for Apollo Dashboard detection...", "cyan"))
        
        try:
            page.wait_for_function(
                "() => window.location.href.includes('#/home') || window.location.href.includes('mailbox') || window.location.href.includes('callback')",
                timeout=600000 # Increase to 10 minutes for slow manual input
            )
            logger.info(colored("✅ Session detected! Letting state settle...", "green", attrs=["bold"]))
            time.sleep(8) # Significant pause to let session tokens settle
            page.wait_for_load_state("networkidle", timeout=30000)
            logger.info(colored("✅ Session fully saved.", "green"))
        except Exception as e:
            logger.error(f"Manual login timed out or failed. Error: {e}")
            raise e

    def direct_login(self):
        page = self.page
        page.fill('input[name="email"]', self.config["username"])
        page.fill('input[name="password"]', self.config["password"])
        page.click('button[type="submit"]')
        page.wait_for_url("**/#/home", timeout=30000)

    def close(self):
        if self.browser: self.browser.close()
        if self.playwright: self.playwright.stop()

class ApolloSessionManager:
    def __init__(self):
        self.session = None

    def get_session(self, email, password=None, login_method="google"):
        config = {
            "username": email,
            "password": password,
            "login_method": login_method
        }
        self.session = ApolloSession(handle=email, config=config)
        self.session.init_browser(headless=False) # Always false for interactive login if needed
        return self.session

    def close(self):
        if self.session:
            self.session.close()
