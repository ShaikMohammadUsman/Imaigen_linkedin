# view_health_stats.py
import logging
from linkedin.usage_tracker import UsageTracker
from linkedin.conf import ASSETS_DIR, list_active_accounts
from termcolor import colored

# Setup minimal logging
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

def print_health_report():
    tracker = UsageTracker(ASSETS_DIR)
    accounts = list_active_accounts()
    
    print(colored("\n" + "="*60, "cyan"))
    print(colored("🛡️  SCOOTER AI: ACCOUNT HEALTH & STEALTH REPORT", "cyan", attrs=["bold"]))
    print(colored("="*60 + "\n", "cyan"))
    
    if not accounts:
        print("No active accounts found in secrets.")
        return

    for handle in accounts:
        print(colored(f"👤 Account: {handle}", "white", attrs=["bold"]))
        
        # 🟢 General Usage Stats
        daily_searches = tracker.get_count(handle, "people_searches", "daily")
        daily_cards = tracker.get_count(handle, "harvested_cards", "daily")
        daily_enrich = tracker.get_count(handle, "enrich_profiles", "daily")
        
        # 🏥 Health Stats
        health = tracker.get_health_stats(handle, timeframe="daily")
        success = health.get("success", 0)
        captchas = health.get("captcha", 0)
        restricted = health.get("restricted", 0)
        timeouts = health.get("timeout", 0)
        failures = health.get("unknown_failure", 0)
        
        total_actions = success + captchas + restricted + timeouts + failures
        success_rate = (success / total_actions * 100) if total_actions > 0 else 100
        
        # Determine Status Color
        status_color = "green"
        status_text = "HEALTHY"
        if captchas > 0 or restricted > 0:
            status_color = "red"
            status_text = "AT RISK / RESTRICTED"
        elif timeouts > 2 or failures > 2:
            status_color = "yellow"
            status_text = "UNSTABLE"

        print(f"Status: ", end="")
        print(colored(status_text, status_color, attrs=["bold"]))
        
        print(f"Daily Usage:")
        print(f"  - Searches: {daily_searches}")
        print(f"  - Leads Harvested: {daily_cards}")
        print(f"  - Profiles Enriched: {daily_enrich}")
        
        print(f"Automation Metrics:")
        print(f"  - Success Rate: {success_rate:.1f}%")
        print(f"  - Successes: {success}")
        print(f"  - Challenges (CAPTCHA): ", end="")
        print(colored(str(captchas), "red" if captchas > 0 else "white"))
        print(f"  - Restrictions: ", end="")
        print(colored(str(restricted), "red" if restricted > 0 else "white"))
        print(f"  - Timeouts: {timeouts}")
        
        if "last_failure_note" in health:
            print(colored(f"  - Last Error: {health['last_failure_note']}", "yellow"))
            
        print("-" * 40)

    print(colored("\nCheck 'usage_stats.json' for full historical data.\n", "dark_grey"))

if __name__ == "__main__":
    print_health_report()
