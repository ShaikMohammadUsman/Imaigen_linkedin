
import json
import logging
import datetime
from pathlib import Path
from termcolor import colored

# Default Limits (Ultra Safe)
HARVEST_PAGE_LIMIT = 50  # Increased from 25 for today's extended run
ENRICH_PROFILE_LIMIT = 30 # Max profile visits per day per account (User Requested 30)

logger = logging.getLogger(__name__)

class UsageTracker:
    def __init__(self, assets_dir: Path):
        self.stats_file = assets_dir / "usage_stats.json"
        self._ensure_file()

    def _ensure_file(self):
        if not self.stats_file.exists():
            with open(self.stats_file, "w") as f:
                json.dump({}, f)

    def _get_today_str(self):
        return datetime.date.today().isoformat()

    def _load_stats(self):
        try:
            with open(self.stats_file, "r") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_stats(self, stats):
        with open(self.stats_file, "w") as f:
            json.dump(stats, f, indent=2)

    def get_todays_count(self, handle: str, metric: str) -> int:
        """
        metric: 'harvest_pages' or 'enrich_profiles'
        """
        stats = self._load_stats()
        today = self._get_today_str()
        
        user_stats = stats.get(handle, {})
        day_stats = user_stats.get(today, {})
        
        return day_stats.get(metric, 0)

    def increment(self, handle: str, metric: str):
        stats = self._load_stats()
        today = self._get_today_str()
        
        if handle not in stats: stats[handle] = {}
        if today not in stats[handle]: stats[handle][today] = {}
        
        current = stats[handle][today].get(metric, 0)
        stats[handle][today][metric] = current + 1
        
        self._save_stats(stats)
        return current + 1

    def check_harvest_safety(self, handle: str) -> bool:
        count = self.get_todays_count(handle, "harvest_pages")
        if count >= HARVEST_PAGE_LIMIT:
            logger.warning(colored(f"🛑 HARVEST LIMIT REACHED for {handle}: {count}/{HARVEST_PAGE_LIMIT} pages today.", "red", attrs=["bold"]))
            return False
        
        remaining = HARVEST_PAGE_LIMIT - count
        logger.info(colored(f"📊 Daily Harvest Usage: {count}/{HARVEST_PAGE_LIMIT} pages. (Safe to go: {remaining} more)", "cyan"))
        return True

    def check_enrich_safety(self, handle: str) -> bool:
        count = self.get_todays_count(handle, "enrich_profiles")
        if count >= ENRICH_PROFILE_LIMIT:
            logger.warning(colored(f"🛑 ENRICHMENT LIMIT REACHED for {handle}: {count}/{ENRICH_PROFILE_LIMIT} profiles today.", "red", attrs=["bold"]))
            return False
        
        remaining = ENRICH_PROFILE_LIMIT - count
        logger.info(colored(f"📊 Daily Enrichment Usage: {count}/{ENRICH_PROFILE_LIMIT} profiles. (Safe to go: {remaining} more)", "cyan"))
        return True
