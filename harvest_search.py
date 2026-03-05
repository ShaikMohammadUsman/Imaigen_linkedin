import logging
import sys
import csv
import json
import time
import random
from pathlib import Path
from urllib.parse import urlparse, parse_qs, urlencode, urljoin

from termcolor import colored
from linkedin.conf import ASSETS_DIR
from linkedin.sessions.registry import get_session
from linkedin.navigation.utils import goto_page
from linkedin.usage_tracker import UsageTracker

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("Harvester")

def harvest_search_results(
    handle: str, search_url: str, start_page: int = 1, max_pages: int = 5, 
    output_file: str = "harvested_urls.csv", 
    job_id: str = "", role_name: str = "", company_name: str = "", app_link: str = "",
    location: str = "", compensation: str = ""
):
    """
    Navigates to a LinkedIn search URL, paginates, extracts profile URLs,
    and saves them to a CSV file.
    
    SAFETY LIMIT: Capped to 6 pages max per run to avoid Commercial Use Limit triggers.
    """

    # Operating Hours Check (09:30 - 21:30 IST)
    # Metadata IST is UTC+5.5
    from datetime import datetime, timezone, timedelta
    ist = timezone(timedelta(hours=5, minutes=30))
    now_ist = datetime.now(ist)
    if now_ist.hour < 9 or (now_ist.hour == 9 and now_ist.minute < 30) or now_ist.hour >= 21:
        logger.error(f"🛑 Outside of Operating Hours (09:30-21:30 IST). Current IST: {now_ist.strftime('%H:%M')}. Aborting.")
        return

    # Init Tracker
    tracker = UsageTracker(ASSETS_DIR)

    # 🟢 Continuation Logic
    if start_page == 1:
        last_page = tracker.get_last_page(handle, search_url)
        if last_page > 0:
            start_page = last_page + 1
            logger.info(colored(f"🔄 Continuing search from page {start_page} (Last was {last_page})", "cyan"))

    # 🟢 Randomized Session Page Limit (Max 6)
    session_limit = tracker.get_session_page_limit(handle)
    # If using automated default (1) or legacy default (>=5), apply randomization
    if max_pages == 1 or max_pages >= 5: 
        max_pages = session_limit
        logger.info(colored(f"🎲 Randomized Session Limit: {max_pages} pages for this run.", "cyan"))

    if max_pages > 6:
        logger.warning(f"⚠️  Capping session to 6 pages for safety.")
        max_pages = 6
    
    # Check People Search Safety
    if not tracker.check_safety(handle, "people_searches", "people_searches"):
        logger.error("🛑 People Search Daily Limit Reached. Aborting for safety.")
        return

    session = get_session(handle)
    session.ensure_browser()
    page = session.page

    # Navigate to the initial search URL
    if start_page > 1:
        separator = "&" if "?" in search_url else "?"
        search_url = f"{search_url}{separator}page={start_page}"
        
    goto_page(
        session,
        action=lambda: page.goto(search_url),
        expected_url_pattern="/search/results/",
        error_message="Failed to load search results",
        to_scrape=False # We handle scraping manually here
    )

    # 🟢 Increment search usage only AFTER successful navigation to results
    tracker.increment(handle, "people_searches")

    # Load existing URLs to avoid duplicates
    collected_urls = set()
    output_path = ASSETS_DIR / "inputs" / output_file
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    file_exists = output_path.exists()
    if file_exists:
        try:
            with open(output_path, "r", encoding="utf-8") as f:
                reader = csv.reader(f)
                next(reader, None) # Skip header
                for row in reader:
                    if row:
                        raw_url = row[0].strip()
                        # Normalize consistently: strip query params and trailing slashes
                        p = urlparse(raw_url)
                        norm_url = f"{p.scheme}://{p.netloc}{p.path}".rstrip("/")
                        collected_urls.add(norm_url)
            logger.info(f"Loaded {len(collected_urls)} unique normalized URLs from {output_file}. Will skip duplicates.")
        except Exception as e:
            logger.warning(f"Could not read existing file: {e}")

    for page_num in range(start_page, start_page + max_pages):
        # 🟢 Safety Check for Card Volume
        if not tracker.check_safety(handle, "harvested_cards", "harvested_cards"):
             logger.warning("🛑 Daily Card Harvesting Limit reached. Stopping.")
             break
        
        # Log progress for continuation
        tracker.update_last_page(handle, search_url, page_num)
        
        # Log this page as a 'visit' (legacy tracker used this)
        tracker.increment(handle, "harvest_pages")
        
        logger.info(f"--- Processing Page {page_num} ---")
        
        # Scroll down to ensure all lazy-loaded elements appear
        for _ in range(3):
            # Human scroll with randomness
            page.mouse.wheel(0, random.randint(800, 1200))
            time.sleep(random.uniform(1.2, 2.5))
        
        # Dynamic Smart Wait with Retries
        found_results = False
        retry_delays = [random.uniform(2.5, 4.0), random.uniform(4.0, 6.0), random.uniform(6.0, 8.0)]
        
        for attempt, delay in enumerate(retry_delays):
            logger.debug(f"Wait attempt {attempt+1}: Waiting {delay:.2f}s for results...")
            try:
                # Wait for results with a short timeout per attempt
                page.wait_for_selector(
                    '.reusable-search__result-container, .entity-result, .search-results__no-results, li.reusable-search__result-container, [data-chameleon-result-id]', 
                    timeout=int(delay * 1000)
                )
                found_results = True
                logger.debug("✅ Results loaded!")
                break
            except Exception:
                # If not found, scroll a bit to trigger lazy loading
                logger.debug("Elements not ready yet. Scrolling to trigger load...")
                page.mouse.wheel(0, random.randint(300, 700))
                time.sleep(random.uniform(1.0, 2.0))
        
        if not found_results:
            grace_period = random.uniform(7.0, 11.5)
            logger.info(colored(f"⏳ [SAFE PACING] Content loading slowly. Waiting {grace_period:.1f}s for full render...", "yellow"))
            time.sleep(grace_period)
        
        # Strategy: Try multiple selectors for search result cards
        # LinkedIn frequently changes class names, so we try several patterns
        result_cards = page.locator(
            'li.reusable-search__result-container, '
            '.reusable-search__result-container, '
            '.entity-result, '
            '[data-chameleon-result-id], '
            '.search-results__cluster-item, '
            'div[class*="entity-result"], '
            'li:has(.entity-result), '
            'div[data-view-name*="search-entity"]'
        )
        count = result_cards.count()
        
        # If still no results, try a broader search
        if count == 0:
            logger.warning("Primary selectors missed cards. Trying deep-DOM scan...")
            # Look for ANY container that has a link starting with /in/
            result_cards = page.locator('li:has(a[href*="/in/"]), div:has(> a[href*="/in/"]), [class*="result-card"]')
            count = result_cards.count()
            logger.info(f"Deep scan found {count} potential cards.")
        
        if count == 0:
            # Check for common "No Results" or "Limit Reached" text
            content = page.content().lower()
            if "no results found" in content or "no results for" in content:
                logger.warning("🔍 LinkedIn reported: 'No results found'. Check your search URL/filters.")
            elif "commercial use limit" in content:
                logger.error("🚨 COMMERCIAL USE LIMIT REACHED! LinkedIn is hiding search results for this account.")
            else:
                logger.warning(f"⚠️ No results found on page {page_num}. Page might still be loading or selector is failing.")
                # Log available elements for debugging
                logger.debug(f"Page URL: {page.url}")
                links_count = len(page.locator('a[href*="/in/"]').all())
                logger.debug(f"Page has {links_count} total profile links")
        
        logger.info(f"Scanning {count} search result cards.")

        new_urls_on_page = []
        seen_on_page = set()

        for i in range(count):
            try:
                card = result_cards.nth(i)
                if not card.is_visible(): continue
                
                # 🟢 Human-like "Skimming" Pause
                # Mimics a user looking at a result for a moment before moving to next
                skim_delay = random.uniform(0.6, 1.8)
                logger.debug(f"  [🔍] Skimming result {i+1}... ({skim_delay:.2f}s)")
                
                # Briefly hover to trigger any visual states/lazy loads
                try:
                    card.hover(timeout=2000)
                except:
                    pass
                
                time.sleep(skim_delay)

                # Extract URL
                link_el = card.locator('a[href*="/in/"]').first
                if not link_el.count(): continue
                
                raw_url = link_el.get_attribute("href")
                if not raw_url: continue
                
                full_url = urljoin(page.url, raw_url)
                if "/in/me" in full_url: continue
                
                # Normalize URL
                parsed = urlparse(full_url)
                clean_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")
                
                if clean_url in seen_on_page:
                    continue
                seen_on_page.add(clean_url)

                if clean_url not in collected_urls:
                    # Extract Name with multiple fallback selectors
                    name = "Harvested Profile"
                    name_selectors = [
                        '.entity-result__title-text a span[aria-hidden="true"]',
                        '.app-aware-link > span[aria-hidden="true"]',
                        '.entity-result__title-text span:first-child',
                        'span.entity-result__title-text',
                        'a[href*="/in/"] span[dir="ltr"]'
                    ]
                    
                    for selector in name_selectors:
                        name_el = card.locator(selector).first
                        if name_el.count():
                            try:
                                name_text = name_el.inner_text().strip()
                                if name_text and len(name_text) > 1:
                                    name = name_text.split('\n')[0]
                                    if "LinkedIn Member" in name:
                                        logger.debug(f"  [i] Restricted profile found: {name}")
                                    break
                            except:
                                continue
                    
                    # Extract Picture with multiple fallback selectors
                    pic_url = ""
                    pic_selectors = [
                        'img.presence-entity__image',
                        'img[class*="EntityPhoto"]',
                        'img.ivm-view-attr__img--centered',
                        'div.presence-entity img',
                        'img[alt*="Photo"]'
                    ]
                    
                    for selector in pic_selectors:
                        img_el = card.locator(selector).first
                        if img_el.count():
                            try:
                                pic_url = img_el.get_attribute("src") or ""
                                if pic_url:
                                    break
                            except:
                                continue

                    collected_urls.add(clean_url)
                    new_urls_on_page.append({
                        "url": clean_url,
                        "name": name,
                        "picture": pic_url
                    })
                    tracker.increment(handle, "harvested_cards")
                    print(f"  [+] Found New: {name} ({clean_url})")
                else:
                    logger.debug(f"  [-] Skipping Duplicate: {clean_url}")
            except Exception as e:
                logger.debug(f"Error parsing card {i}: {e}")
        
        if len(new_urls_on_page) == 0 and len(seen_on_page) == 0:
            logger.warning("❌ No profile URLs found on page!")
            try:
                screenshot_path = f"debug_harvest_page_{page_num}.png"
                page.screenshot(path=screenshot_path)
                logger.info(f"Saved debug screenshot to {screenshot_path}")
                
                # Check for Authwall / Security
                content = page.content().lower()
                # More specific captcha/security check detection
                is_captcha = any(x in content for x in ["/checkpoint/challenge/", "captcha-container", "recaptcha-script"])
                is_authwall = "authwall" in content or "login.php" in content or ("Sign In" in page.content() and "Join now" in page.content())
                
                if is_authwall:
                    logger.error("🚨 Hit Authwall or Login Check! The session may have expired.")
                    break
                if is_captcha or (("security check" in content or "captcha" in content) and "challenge" in page.url):
                    logger.error("🚨 SECURITY CHECK/CAPTCHA DETECTED! Use VNC to solve the captcha manually.")
                    break
                
                # If we found nothing but it's clearly a search page, it's a "Network Restriction"
                if "search" in page.url and "LinkedIn Member" in page.content():
                    logger.warning("🔍 All results are 'LinkedIn Member'. You are seeing out-of-network profiles or have hit a search volume limit.")
            except Exception:
                pass
        
        # Save State (Last Page Scraped)
        try:
            state_file = ASSETS_DIR / "inputs" / "harvest_state.json"
            if state_file.exists():
                with open(state_file, "r") as f:
                    state = json.load(f)
            else:
                state = {}
            
            # Key: Combined Role+Company to be unique
            key = f"{role_name}|{company_name}"
            state[key] = page_num
            
            with open(state_file, "w") as f:
                json.dump(state, f, indent=2)
            logger.info(f"Saved checkout state: '{key}' -> Page {page_num}")
        except Exception as e:
            logger.warning(f"State save failed: {e}")


        logger.info(f"Extracted {len(new_urls_on_page)} NEW URLs from page {page_num}.")

        # Save progress immediately (Append Mode)
        if new_urls_on_page:
            mode = "a" if file_exists else "w"
            with open(output_path, mode, encoding="utf-8", newline="") as f:
                writer = csv.writer(f)
                if not file_exists:
                    writer.writerow(["url", "job_id", "role_name", "company_name", "app_link", "location", "compensation", "candidate_name", "candidate_pic"]) # Write header
                    file_exists = True # Now it exists
                
                for item in new_urls_on_page:
                    writer.writerow([
                        item["url"], job_id, role_name, company_name, 
                        app_link, location, compensation, 
                        item["name"], item["picture"]
                    ])

        
        # 🟢 Scroll to Bottom to find Next Button
        logger.info("Scrolling to bottom for pagination...")
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(2)

        # Check for 'Next' button
        if page_num < (start_page + max_pages - 1):
            try:
                # Try multiple common LinkedIn Next button selectors
                next_selectors = [
                    'button[aria-label="Next"]',
                    'a[aria-label="Next"]',
                    '.artdeco-pagination__button--next',
                    'button:has-text("Next")',
                    'a:has-text("Next")'
                ]
                
                found_next = False
                for selector in next_selectors:
                    btn = page.locator(selector).first
                    if btn.count() and btn.is_visible():
                        logger.info(f"Found 'Next' via: {selector}")
                        btn.click()
                        found_next = True
                        break
                
                if found_next:
                    # 🟢 Ultra-Conservative Randomized Pause (17-43s)
                    delay = random.uniform(17, 43)
                    logger.info(colored(f"⏳ Human-like Pause: {delay:.2f}s before next page...", "yellow"))
                    time.sleep(delay)
                else:
                    logger.info("No 'Next' button found or end of results. Stopping.")
                    break
            except Exception as e:
                 logger.info(f"Error finding Next button: {e}. Stopping.")
                 break

    logger.info(f"Harvesting complete! Saved {len(collected_urls)} URLs to {output_path}")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python harvest_search.py <handle> <search_url> [start_page] [pages] [job_id] [role_name] [company] [link] [loc] [comp]")
        sys.exit(1)

    handle = sys.argv[1]
    search_url = sys.argv[2]
    start_page = int(sys.argv[3]) if len(sys.argv) > 3 else 1
    max_pages = int(sys.argv[4]) if len(sys.argv) > 4 else 5
    job_id = sys.argv[5] if len(sys.argv) > 5 else ""
    role_name = sys.argv[6] if len(sys.argv) > 6 else ""
    
    # Simple fix for potential empty strings passed as argument " "
    company_name = sys.argv[7] if len(sys.argv) > 7 else ""
    app_link = sys.argv[8] if len(sys.argv) > 8 else ""
    location = sys.argv[9] if len(sys.argv) > 9 else ""
    compensation = sys.argv[10] if len(sys.argv) > 10 else ""

    harvest_search_results(
        handle, search_url, start_page, max_pages, 
        job_id=job_id, role_name=role_name,
        company_name=company_name, app_link=app_link,
        location=location, compensation=compensation
    )
