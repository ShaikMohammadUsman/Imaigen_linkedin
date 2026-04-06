# scooter_clay/harvester.py
import logging
import time
import random
import csv
from termcolor import colored
from linkedin.usage_tracker import UsageTracker
from linkedin.conf import ASSETS_DIR

logger = logging.getLogger("ClayHarvester")

def harvest_clay_leads(session, workbook_url, limit=10):
    page = session.page
    tracker = UsageTracker(ASSETS_DIR)
    tracker.record_session(session.handle)
    
    logger.info(colored(f"📡 Navigating to Clay Workbook: {workbook_url}", "blue"))
    
    try:
        page.goto(workbook_url, wait_until="networkidle", timeout=90000)
    except:
        page.goto(workbook_url, wait_until="domcontentloaded", timeout=60000)
        
    # Wait for the table to actually appear with aTimeout
    try:
        page.wait_for_selector('tr, td, [role="row"], [role="gridcell"]', timeout=30000)
        logger.info("✅ Table content detected.")
    except Exception as e:
        logger.warning(f"⚠️ Timeout waiting for table content: {e}")

    # Let the table load more content/animations
    time.sleep(5)
    
    logger.info("🔭 Scanning the Clay table...")
    page.screenshot(path="debug_clay_load.png")
    
    # High-level selectors for Clay rows (educated guesses based on typical SaaS grids)
    row_selectors = [
        'tr',  # Standard Table Row
        '.clay-table-row',
        '[role="row"]',
        '.grid-row',
        '.rdg-row',
        'div[data-row-index]', # Specific to some grid versions
        '.Table__row',
        'div.flex.w-full' # Fallback for grid-less flex layouts
    ]
    
    rows = []
    best_selector = None
    
    for sel in row_selectors:
        found_rows = page.locator(sel).all()
        if len(found_rows) > 1: # More than just a header
            # Briefly check if any row has text
            has_content = False
            for r in found_rows[:5]:
                if r.inner_text().strip():
                    has_content = True
                    break
            
            if has_content:
                rows = found_rows
                best_selector = sel
                logger.info(f"✅ Found {len(rows)} potential rows using selector: {sel}")
                break
            
    if not rows:
        # Emergency fallback: Look for ANY elements that might contain a LinkedIn link
        logger.warning("⚠️ No standard rows found. Using broad element search...")
        rows = page.locator('div:has-text("linkedin.com/in/"), tr:has-text("linkedin.com/in/")').all()
        if rows:
             logger.info(f"✅ Found {len(rows)} elements containing LinkedIn URLs.")
             best_selector = "broad_search"

    if not rows:
        logger.error(colored("❌ Could not find any rows in the Clay table.", "red"))
        with open("debug_clay_dom.html", "w") as f:
            f.write(page.content())
        return []

    all_leads = []
    
    # Process rows up to limit
    for i in range(len(rows)):
        if len(all_leads) >= limit: break
        
        row = rows[i]
        try:
            # Clay is very dynamic. Sometimes we need to focus/hover to get text.
            row.scroll_into_view_if_needed()
            
            # Extract cells - try to find anything that looks like a cell or container
            cells = row.locator('td, [role="gridcell"], div[role="cell"], .clay-cell, div, p, span, a').all()
            
            linkedin_url = ""
            name = "Clay lead"
            location = "-"
            location_hooks = ["india", "usa", "uk", "pune", "mumbai", "london", "area", "division", "state", "city"]
            
            for c in cells:
                # 1. Check direct href (if the cell itself is a link)
                href = c.get_attribute("href")
                if href and "linkedin.com/in/" in href.lower():
                    linkedin_url = href
                    break

                # 2. Check inner text
                text = c.inner_text().strip()
                if not text: continue
                
                if "linkedin.com/in/" in text.lower():
                    # Extract URL from messy text
                    if " " in text:
                        words = text.replace('"', '').replace("'", "").split()
                        for w in words:
                            if "linkedin.com/in/" in w.lower():
                                linkedin_url = w.strip()
                                break
                    else:
                        linkedin_url = text
                    if linkedin_url: break
                
                # 3. Check for children that might be links (nested <a>)
                try:
                    nested_link = c.locator('a[href*="linkedin.com/in/"]').first
                    if nested_link.count() > 0:
                        linkedin_url = nested_link.get_attribute("href")
                        if linkedin_url: break
                except: pass
                
                # 4. Try to guess Name or Location
                if not linkedin_url and 2 < len(text) < 60 and " " in text and "@" not in text and "http" not in text:
                    # Heuristic: Locations often have commas or specific keywords
                    is_loc = "," in text or any(h in text.lower() for h in location_hooks)
                    
                    if is_loc:
                        location = text
                    else:
                        # If it's a short string without location hooks, it's likely the name
                        if len(text.split()) <= 4: # Names are rarely > 4 words
                             name = text
                
                # 5. Backup Name detection if we haven't found one
                if name == "Clay lead" and not linkedin_url and 2 < len(text) < 30 and " " in text and not any(x in text.lower() for x in location_hooks):
                    name = text

            if linkedin_url:
                # Cleanup name if it's still generic but we have location
                if name == "Clay lead" and location != "-":
                     # Sometimes the name is actually combined with location in one cell or we missed it
                     pass

                all_leads.append({
                    "url": linkedin_url,
                    "candidate_name": name,
                    "location": location,
                    "role_name": "Clay Export"
                })
                logger.info(f"  [+] Captured: {name} ({location}) | {linkedin_url}")
            
            time.sleep(0.5)
        except Exception as e:
            logger.warning(f"  [!] Error parsing row: {e}")
            continue

    # Save to main Queue
    save_to_main_queue(all_leads)
    UsageTracker(ASSETS_DIR).record_health_event(session.handle, "success")
    return all_leads

def save_to_main_queue(leads):
    if not leads: return
    
    # Path relative to script execution (usually project root)
    output_path = Path("assets/inputs/harvested_urls.csv")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    fieldnames = ["url", "job_id", "role_name", "company_name", "app_link", "location", "compensation", "candidate_name", "candidate_pic", "source"]
    
    # Check for existing to avoid duplicates
    existing_urls = set()
    if output_path.exists():
        with open(output_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                existing_urls.add(row.get("url"))

    # Append new ones
    mode = "a" if output_path.exists() else "w"
    with open(output_path, mode, newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
        if mode == "w":
            writer.writeheader()
        
        count = 0
        for lead in leads:
            if lead["url"] not in existing_urls:
                lead["source"] = "Clay"
                writer.writerow(lead)
                existing_urls.add(lead["url"])
                count += 1
                
    logger.info(colored(f"✅ Successfully added {count} NEW leads to persistent queue (Source: Clay).", "green", attrs=["bold"]))

from pathlib import Path
