# scooter_apollo/harvester.py
import logging
import random
import time
import csv
from termcolor import colored
from linkedin.usage_tracker import UsageTracker
from linkedin.conf import ASSETS_DIR

logger = logging.getLogger("ApolloHarvester")

def clean_apollo_text(text):
    if not text: return ""
    # Remove the anti-bot "word waterfall" patterns
    cleaned = text.replace("word word word", "").replace("word ", "").replace("mMwWL", "")
    # Remove excessive newlines and spaces
    return " ".join(cleaned.split()).strip()

ROW_SELECTORS = [
    '[data-cy="prospect-table-row"]',
    '.zp-contact-row',
    '.zp_row'
]

def dismiss_popups(page):
    """Automatically dismiss common Apollo popups/overlays."""
    popups = [
        'button:has-text("Close")',
        'button:has-text("Skip")',
        'button:has-text("Maybe Later")',
        'button:has-text("Dismiss")',
        'button:has-text("Got it")',
        '[aria-label="Close"]',
        '.zp-modal-close',
        '.zp-overlay-close',
        'div[role="button"]:has-text("Close")',
        '#paragon-connect-frame button:has-text("Close")', # Paragon integration
        'button:has-text("Remind me later")',
        'a:has-text("Remind me later")',
        '.zp_DE9_M', # Common Apollo close icon
        'button:has-text("Go to Homepage")'
    ]

    # --- DOM CLEANER: DEACTIVATED ---
    # We stopped doing this to prevent white-screens.
    # The toxic words are now filtered in clean_apollo_text() instead.

    for sel in popups:
        try:
            if page.locator(sel).count() > 0:
                if page.locator(sel).first.is_visible(timeout=1000):
                    logger.info(colored(f"🧹 Dismissing Apollo popup: {sel}", "magenta"))
                    page.locator(sel).first.click()
                    time.sleep(1)
        except: continue

def harvest_apollo_leads(session, search_url, pages=1):
    page = session.page
    tracker = UsageTracker(ASSETS_DIR)
    tracker.record_session(session.handle)
    
    logger.info(colored(f"📡 Navigating to Apollo Search: {search_url}", "blue"))
    page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
    
    # Let page settle
    time.sleep(10)
    dismiss_popups(page)
    
    # 1. Detect and Bypass the "Link Mailbox" Lockout
    lockout_indicators = ["gmail_oauth_callback", "mailbox", "onboarding", "connect-hub"]
    
    # Buster for the "Go to Homepage" purple button and Onboarding blocks
    if any(x in page.url for x in lockout_indicators):
        logger.warning(colored("⛔ Detected Onboarding/Mailbox Lockout. Attempting to bypass...", "yellow", attrs=["bold"]))
        
        # Try clicking "Go to Homepage" if visible
        homepage_btn = page.locator('button:has-text("Go to Homepage"), a:has-text("Go to Homepage")').first
        if homepage_btn.count() > 0 and homepage_btn.is_visible():
            homepage_btn.click()
            time.sleep(5)
            
        # Try clicking any Close/Skip buttons
        dismiss_popups(page)
        
        # Final Force Bypass
        if any(x in page.url for x in lockout_indicators):
            page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
            time.sleep(10)
    
    # Force a scroll to trigger results
    page.evaluate("window.scrollBy(0, 500)")
    time.sleep(2)

    row_selectors = [
        'tr[role="row"]',
        'div[role="row"]',
        '.zp-contact-row',
        '.zp_row',
        '[data-cy="prospect-table-row"]',
        '.zp-contact-table-row'
    ]
    
    # 2. Loop to handle dynamic redirects and loading
    for attempt in range(4):
        # Specific check for the Mailbox Link Lockout OR being stuck at home
        is_stuck = any(x in page.url for x in ["gmail_oauth_callback", "mailbox", "onboarding", "connect-hub"])
        is_at_home = "#/home" in page.url or "Welcome" in page.content()
        
        if is_stuck or is_at_home or ("people" not in page.url and attempt == 0):
             logger.warning(colored(f"⛔ Apollo Redirected/Stuck (At: {page.url}). Attempting Sidebar/Force Bypass...", "yellow"))
             
             # Try clicking the Search icon in the sidebar (Usually the 2nd icon)
             try:
                 sidebar_search = page.locator('a[href*="#/people"], .fa-search, [data-cy="nav-item-people"]').first
                 if sidebar_search.count() > 0:
                     sidebar_search.click()
                     time.sleep(5)
             except: pass
             
             if "people" not in page.url:
                 page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
                 time.sleep(12)
                 
             dismiss_popups(page)

        try:
            logger.info(f"⌛ Waiting for leads to appear (Attempt {attempt+1})...")
            page.screenshot(path=f"debug_apollo_attempt_{attempt+1}.png")
            selector = ", ".join(ROW_SELECTORS)
            # Wait for either a row OR the "No results found" container
            page.wait_for_selector(f"{selector}, [data-cy=\"empty-state-container\"]", timeout=20000)
            break 
        except:
            if attempt == 3:
                logger.error(colored(f"❌ Could not find search results. Last try: Reloading.", "red"))
                page.reload()
                time.sleep(15)
                # Check for leads one last time after reload (BROAD SELECTOR)
                rows_locator = page.locator(", ".join(ROW_SELECTORS))
                if rows_locator.count() > 0:
                    break
                    
                # DESPERATION: Find ANY row-like element
                rows_locator = page.locator('tr, [role="row"], .zp-contact-row').filter(has_text="Access email")
                if rows_locator.count() > 0:
                    break
                    
                with open("debug_apollo_dom.html", "w") as f:
                    f.write(page.content())
                page.screenshot(path="debug_apollo_failure.png")
                return []
            
            logger.info("   [~] No leads yet. Scrolling and waiting...")
            page.evaluate("window.scrollBy(0, 400)")
            time.sleep(8)

    all_leads = []

    for p in range(1, pages + 1):
        logger.info(colored(f"📄 Processing Apollo Page {p}", "yellow"))
        
        # Ensure rows are actually present before count
        rows_locator = page.locator(", ".join(ROW_SELECTORS))
        try:
            # First row MUST be visible
            rows_locator.first.wait_for(state="visible", timeout=30000)
        except:
            # FALLBACK: Try to find rows based on the "Access email" button which is very stable
            logger.info("   [~] Primary rows not found. Trying fallback via 'Access email' buttons...")
            rows_locator = page.locator('tr, [role="row"], .zp-contact-row').filter(has_text="Access email")
            
        count = rows_locator.count()
        if count == 0:
            logger.error(colored("❌ Leads did not appear in time. Check leads_missing_error.png.", "red"))
            page.screenshot(path="leads_missing_error.png")
            break
            
        logger.info(colored(f"📊 Found {count} prospects on page {p}.", "cyan"))

        for i in range(count):
            row = rows_locator.nth(i)
            # Skip header rows if detected
            if "Name" in row.inner_text() and i == 0: continue
            
            try:
                # --- Human Mimicry: Skimming ---
                delay = random.uniform(3.5, 6.0) if random.random() < 0.15 else random.uniform(0.7, 1.9)
                row.hover()
                time.sleep(delay)

                # --- SURGICAL EXTRACTION ---
                # Apollo uses data-id attributes for columns
                name_cell = row.locator('[data-id="contact.name"]').first
                title_cell = row.locator('[data-id="contact.job_title"]').first
                company_cell = row.locator('[data-id="contact.account"]').first
                
                name = clean_apollo_text(name_cell.text_content())
                title = clean_apollo_text(title_cell.text_content())
                company = clean_apollo_text(company_cell.text_content())
                
                # If cells are missing (sometimes they use different IDs), fallback to broad search
                if not name or name == "Unknown Name":
                    name_link = row.locator('a[href*="/people/"]').first
                    name = clean_apollo_text(name_link.text_content()) if name_link.count() > 0 else "Unknown Name"
                
                if not company or company == "Unknown Company":
                    company_link = row.locator('a[href*="/accounts/"]').first
                    company = clean_apollo_text(company_link.text_content()) if company_link.count() > 0 else "Unknown Company"
                
                # Find LinkedIn URL
                li_link = row.locator('a[href*="linkedin.com/in/"]').first
                linkedin_url = ""
                if li_link.count() > 0:
                    linkedin_url = li_link.get_attribute("href")
                
                # Final Clean: Remove newlines and excess whitespace which confuses CSVs
                name = " ".join(name.split())
                title = " ".join(title.split())
                company = " ".join(company.split())
                
                lead_data = {
                    "url": linkedin_url,
                    "candidate_name": name.strip(),
                    "role_name": "Apollo Export",
                    "company_name": company.strip()
                }
                
                if linkedin_url:
                    all_leads.append(lead_data)
                    logger.info(f"  [+] Captured: {name} | {linkedin_url}")
                else:
                    logger.warning(f"  [!] Skip: {name} (Missing LinkedIn URL)")
            except Exception as e:
                logger.debug(f"  [!] Skip row {i}: {e}")

        # Pagination
        if p < pages:
            next_btn = page.locator('button[aria-label="Next Page"], .zp-pagination-button:has-text("Next")').first
            if next_btn.is_enabled():
                next_btn.click()
                time.sleep(random.uniform(5, 10))
            else:
                break

    # Save to Main Queue
    save_to_main_queue(all_leads)
    UsageTracker(ASSETS_DIR).record_health_event(session.handle, "success")
    return all_leads

def save_to_main_queue(leads):
    if not leads: return
    
    # Path relative to script execution
    from pathlib import Path
    output_path = Path("assets/inputs/harvested_urls.csv")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    fieldnames = ["url", "job_id", "role_name", "company_name", "app_link", "location", "compensation", "candidate_name", "candidate_pic", "source"]
    
    existing_urls = set()
    if output_path.exists():
        with open(output_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                existing_urls.add(row.get("url"))

    mode = "a" if output_path.exists() else "w"
    with open(output_path, mode, newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
        if mode == "w":
            writer.writeheader()
        
        count = 0
        for lead in leads:
            if lead["url"] not in existing_urls:
                lead["source"] = "Apollo"
                writer.writerow(lead)
                existing_urls.add(lead["url"])
                count += 1
                
    logger.info(colored(f"✅ Successfully added {count} NEW leads to persistent queue (Source: Apollo).", "green", attrs=["bold"]))

def save_to_csv(leads):
    # Backward compatibility
    output_path = "assets/outputs/apollo_harvest.csv"
    ...
    keys = leads[0].keys() if leads else []
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        writer.writerows(leads)
    logger.info(colored(f"✅ Successfully exported {len(leads)} leads to {output_path}", "green", attrs=["bold"]))
