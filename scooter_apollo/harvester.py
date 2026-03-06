# scooter_apollo/harvester.py
import logging
import random
import time
import csv
from termcolor import colored

logger = logging.getLogger("ApolloHarvester")

def harvest_apollo_leads(session, search_url, pages=1):
    page = session.page
    logger.info(colored(f"📡 Navigating to Apollo Search: {search_url}", "blue"))
    page.goto(search_url)

    # Wait for results to load
    page.wait_for_selector('[data-cy="prospect-table-row"], .zp-contact-table-row', timeout=20000)

    all_leads = []

    for p in range(1, pages + 1):
        logger.info(colored(f"📄 Processing Apollo Page {p}", "yellow"))
        
        # Find all lead rows
        rows = page.locator('[data-cy="prospect-table-row"], .zp-contact-table-row')
        count = rows.count()
        logger.info(f"Found {count} prospects on page.")

        for i in range(count):
            row = rows.nth(i)
            
            # --- Human Mimicry: Skimming ---
            if random.random() < 0.15: # 15% Deep Look
                delay = random.uniform(4.0, 7.5)
                logger.info(colored(f"  [👀] Deep parsing lead {i+1}...", "magenta"))
            else:
                delay = random.uniform(0.8, 2.2)
            
            row.hover()
            time.sleep(delay)

            # Extraction Logics (Selectors are illustrative based on common Apollo DOM)
            try:
                name = row.locator('.zp_xZ87b').first.inner_text() # Name selector
                title = row.locator('.zp_Y6y8d').first.inner_text() # Title selector
                company = row.locator('a[href*="/accounts/"]').first.inner_text()
                
                # Optional: Handle "Access Email" button click if needed
                # email_btn = row.locator('button:has-text("Access Email")')
                # if email_btn.count(): email_btn.click(); time.sleep(1)

                lead_data = {
                    "name": name,
                    "title": title,
                    "company": company,
                    "apollo_url": page.url
                }
                all_leads.append(lead_data)
                logger.info(f"  [+] Captured: {name} | {title} @ {company}")
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

    # Save to CSV
    save_to_csv(all_leads)
    return all_leads

def save_to_csv(leads):
    output_path = "assets/outputs/apollo_harvest.csv"
    keys = leads[0].keys() if leads else []
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        writer.writerows(leads)
    logger.info(colored(f"✅ Successfully exported {len(leads)} leads to {output_path}", "green", attrs=["bold"]))
