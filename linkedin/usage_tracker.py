
import json
import logging
import datetime
from pathlib import Path
from termcolor import colored

# Ultra-Conservative Safety Configuration
SAFETY_CONFIG = {
    "people_searches": {
        "monthly_target_range": (80, 120),
        "weekly_target_range": (20, 30),
        "daily_normal": (1, 3),
        "daily_heavy": (3, 4),
        "hard_cap": 5,
        "heavy_days_per_week": 2
    },
    "health": {
        "event_types": ["success", "captcha", "restricted", "timeout", "network_error", "unknown_failure"]
    },
    "harvested_cards": {
        "monthly_target_range": (4000, 6000),
        "weekly_target_range": (1000, 1500),
        "week_1_2_range": (100, 180),
        "week_3_4_range": (180, 250),
        "hard_cap": 500,
        "mature_hard_cap": 300
    },
    "enrich_profiles": {
        "monthly_target_range": (200, 350),
        "weekly_target_range": (50, 80),
        "daily_normal": (0, 10),
        "hard_cap": 13,
        "between_profiles_delay": (30, 75),
        "batch_pause_after": (3, 4),
        "batch_pause_duration": (180, 420)
    },
    "harvest_session": {
        "max_pages_per_run": 6,
        "random_range": (2, 6)
    }
}

# Legacy for backward compatibility
HARVEST_PAGE_LIMIT = 50 
ENRICH_PROFILE_LIMIT = 15 

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

    def _get_month_str(self):
        return datetime.date.today().strftime("%Y-%m")

    def _get_week_str(self):
        # YYYY-WW (Year and ISO week number)
        year, week, _ = datetime.date.today().isocalendar()
        return f"{year}-W{week:02d}"

    def _load_stats(self):
        try:
            with open(self.stats_file, "r") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_stats(self, stats):
        with open(self.stats_file, "w") as f:
            json.dump(stats, f, indent=2)

    def get_count(self, handle: str, metric: str, timeframe="daily") -> int:
        stats = self._load_stats()
        user_stats = stats.get(handle, {})
        
        if timeframe == "daily":
            today = self._get_today_str()
            return user_stats.get(today, {}).get(metric, 0)
        elif timeframe == "weekly":
            week = self._get_week_str()
            return user_stats.get("weekly", {}).get(week, {}).get(metric, 0)
        else:
            month = self._get_month_str()
            return user_stats.get("monthly", {}).get(month, {}).get(metric, 0)

    def increment(self, handle: str, metric: str):
        stats = self._load_stats()
        today = self._get_today_str()
        week = self._get_week_str()
        month = self._get_month_str()
        
        if handle not in stats: 
            stats[handle] = {
                "metadata": {
                    "first_seen": datetime.date.today().isoformat(),
                    "account_type": "new"
                }
            }
        
        # Ensure metadata exists for older accounts
        if "metadata" not in stats[handle]:
            stats[handle]["metadata"] = {"first_seen": datetime.date.today().isoformat()}

        # Daily
        if today not in stats[handle]: stats[handle][today] = {}
        stats[handle][today][metric] = stats[handle][today].get(metric, 0) + 1
        
        # Weekly
        if "weekly" not in stats[handle]: stats[handle]["weekly"] = {}
        if week not in stats[handle]["weekly"]: stats[handle]["weekly"][week] = {}
        stats[handle]["weekly"][week][metric] = stats[handle]["weekly"][week].get(metric, 0) + 1

        # Monthly
        if "monthly" not in stats[handle]: stats[handle]["monthly"] = {}
        if month not in stats[handle]["monthly"]: stats[handle]["monthly"][month] = {}
        stats[handle]["monthly"][month][metric] = stats[handle]["monthly"][month].get(metric, 0) + 1
        
        self._save_stats(stats)
        return stats[handle][today][metric]

    def record_session(self, handle: str):
        stats = self._load_stats()
        today = self._get_today_str()
        
        if handle not in stats: stats[handle] = {}
        if today not in stats[handle]: stats[handle][today] = {}
        
        stats[handle][today]["sessions_started"] = stats[handle][today].get("sessions_started", 0) + 1
        self._save_stats(stats)
        logger.info(colored(f"🏁 Session started for {handle}. Total for today: {stats[handle][today]['sessions_started']}", "blue"))

    def record_health_event(self, handle: str, event_type: str, details: str = None):
        """
        Classifies and records tool/automation failures or successes.
        event_type in: success, captcha, restricted, timeout, network_error, unknown_failure
        """
        stats = self._load_stats()
        today = self._get_today_str()
        
        if handle not in stats: stats[handle] = {}
        if "health" not in stats[handle]: stats[handle]["health"] = {}
        if today not in stats[handle]["health"]: 
            stats[handle]["health"][today] = {et: 0 for et in SAFETY_CONFIG["health"]["event_types"]}
        
        if event_type in stats[handle]["health"][today]:
            stats[handle]["health"][today][event_type] += 1
            
            # Record last failure reason if provided
            if event_type != "success" and details:
                stats[handle]["health"][today]["last_failure_note"] = details[:200]
                
            self._save_stats(stats)
            
            # Log it
            if event_type == "success":
                logger.debug(f"🏥 Health: Recorded success for {handle}")
            else:
                color = "red" if event_type in ["captcha", "restricted"] else "yellow"
                logger.warning(colored(f"🏥 Health Alert: Recorded {event_type} for {handle} ({details or 'No details'})", color))
        else:
            logger.error(f"Invalid health event type: {event_type}")

    def reset_health(self, handle: str):
        """Reset today's health metrics to healthy state."""
        stats = self._load_stats()
        today = self._get_today_str()
        
        if handle in stats and "health" in stats[handle] and today in stats[handle]["health"]:
            stats[handle]["health"][today] = {et: 0 for et in SAFETY_CONFIG["health"]["event_types"]}
            stats[handle]["health"][today]["last_failure_note"] = ""
            stats[handle]["health"][today]["success"] = 1 # Start with 1 success to flip indicator
            self._save_stats(stats)
            logger.info(colored(f"🏥 Health Reset for {handle}. Status now HEALTHY.", "green"))
            return True
        return False

    def get_health_stats(self, handle: str, timeframe="daily"):
        stats = self._load_stats()
        user_health = stats.get(handle, {}).get("health", {})
        
        if timeframe == "daily":
            today = self._get_today_str()
            return user_health.get(today, {})
        
        # Monthly summary
        month_prefix = self._get_month_str()
        monthly_summary = {et: 0 for et in SAFETY_CONFIG["health"]["event_types"]}
        for date, day_stats in user_health.items():
            if date.startswith(month_prefix):
                for et in monthly_summary:
                    monthly_summary[et] += day_stats.get(et, 0)
        
        return monthly_summary

    def get_last_page(self, handle: str, search_url: str) -> int:
        import hashlib
        url_hash = hashlib.md5(search_url.encode()).hexdigest()
        stats = self._load_stats()
        return stats.get(handle, {}).get("search_history", {}).get(url_hash, 0)

    def update_last_page(self, handle: str, search_url: str, page_num: int):
        import hashlib
        url_hash = hashlib.md5(search_url.encode()).hexdigest()
        stats = self._load_stats()
        if handle not in stats: stats[handle] = {}
        if "search_history" not in stats[handle]: stats[handle]["search_history"] = {}
        
        # Only update if it's forward progress or reset
        stats[handle]["search_history"][url_hash] = page_num
        self._save_stats(stats)

    def get_session_page_limit(self, handle: str) -> int:
        """
        Picks a randomized page limit for the current execution session.
        Mimics user deciding to browse 2, 3, or 4 pages.
        """
        import random
        import hashlib
        import datetime
        
        # We want the 'session' randomness to be different even on the same day if called multiple times,
        # but maybe somewhat consistent? Actually pure random is fine for 'sessions'.
        # However, to avoid picking a huge number, we cap at config max.
        config = SAFETY_CONFIG["harvest_session"]
        
        # Use a more high-resolution seed for session randomness
        seed_str = f"{datetime.datetime.now().isoformat()}-{handle}-session"
        seed = int(hashlib.md5(seed_str.encode()).hexdigest(), 16) % 10000
        rng = random.Random(seed)
        
        return rng.randint(*config["random_range"])

    def get_session_limit(self, handle: str, category: str) -> int:
        """
        Calculates a session-specific limit (per click/run).
        """
        import random
        import hashlib
        import datetime
        
        seed_str = f"{datetime.datetime.now().strftime('%Y-%m-%d-%H')}-{handle}-{category}-session"
        seed = int(hashlib.md5(seed_str.encode()).hexdigest(), 16) % 10000
        rng = random.Random(seed)

        limit = 0
        if category == "people_searches":
            limit = rng.randint(1, 2)
        elif category == "harvested_cards":
            limit = rng.randint(30, 60)
        elif category == "enrich_profiles":
            limit = rng.randint(2, 4)
            
        logger.info(colored(f"🎲 [SESSION GOAL] Determined session limit for {category}: {limit} (Based on human-mimicry randomness)", "magenta"))
        return limit

    def get_dynamic_daily_limit(self, handle: str, metric_category: str) -> int:
        """
        Calculates a randomized daily limit based on the SAFETY_CONFIG and account state.
        """
        import random
        config = SAFETY_CONFIG.get(metric_category)
        if not config: return 0
        
        # Deterministic seed based on date + handle to keep limit consistent for the day
        import hashlib
        seed_str = f"{self._get_today_str()}-{handle}-{metric_category}"
        seed = int(hashlib.md5(seed_str.encode()).hexdigest(), 16) % 10000
        rng = random.Random(seed)

        if metric_category == "people_searches":
            # 0-6: Mon=0, Sun=6
            day_of_week = datetime.date.today().weekday() 
            
            if day_of_week == 6: # Sunday: Off (or almost off)
                return rng.choice([0, 0, 0, 1]) 
            if day_of_week == 5: # Saturday: Very Light
                return rng.randint(0, 2)
                
            # 2-3 heavy days per week (Tue, Thu, Wed randomly)
            is_heavy = day_of_week in [1, 3, 2] # Tue, Thu, Wed
            
            if is_heavy:
                limit = rng.randint(*config["daily_heavy"])
                logger.info(colored(f"📈 [DAILY LIMIT] Tuesday/Wednesday/Thursday detected (Heavy Day). Cap set to: {limit} searches.", "blue"))
            else:
                limit = rng.randint(*config["daily_normal"])
                logger.info(colored(f"📉 [DAILY LIMIT] Normal activity day. Cap set to: {limit} searches.", "blue"))
            return limit
                
        elif metric_category == "harvested_cards":
            # 🟢 Account Maturity Check
            stats = self._load_stats()
            metadata = stats.get(handle, {}).get("metadata", {})
            first_seen_str = metadata.get("first_seen", self._get_today_str())
            
            try:
                first_seen = datetime.date.fromisoformat(first_seen_str)
                days_active = (datetime.date.today() - first_seen).days
            except:
                days_active = 0
            
            # If active > 14 days, use mature range
            if days_active > 14:
                limit = rng.randint(*config["week_3_4_range"])
                logger.info(colored(f"🔓 [MATURITY] Account age: {days_active} days (MATURE). Safe range: {config['week_3_4_range']}. Dynamic Limit: {limit} cards.", "green"))
                return limit
            else:
                limit = rng.randint(*config["week_1_2_range"])
                logger.info(colored(f"🔒 [MATURITY] Account age: {days_active} days (WARM-UP). Safe range: {config['week_1_2_range']}. Dynamic Limit: {limit} cards.", "yellow"))
                return limit
            
        elif metric_category == "enrich_profiles":
            limit = rng.randint(*config["daily_normal"])
            return min(limit, config["hard_cap"])
            
        return 0

    def check_safety(self, handle: str, category: str, metric: str) -> bool:
        """
        Comprehensive safety check: Daily Hard Limit + Monthly Target.
        categories: 'people_searches', 'harvested_cards', 'enrich_profiles'
        """
        daily_count = self.get_count(handle, metric, "daily")
        monthly_count = self.get_count(handle, metric, "monthly")
        
        config = SAFETY_CONFIG.get(category)
        daily_limit = self.get_dynamic_daily_limit(handle, category)
        
        # 1. Monthly Cap Check
        monthly_limit = SAFETY_CONFIG[category].get("monthly_target_range", (999, 999))[1]
        if monthly_count >= monthly_limit:
             logger.warning(colored(f"🛑 MONTHLY LIMIT REACHED for {handle}: {monthly_count}/{monthly_limit} {metric}.", "red", attrs=["bold"]))
             return False

        # 2. Daily Cap Check
        if daily_count >= daily_limit:
            logger.warning(colored(f"🛑 DAILY LIMIT REACHED for {handle}: {daily_count}/{daily_limit} {metric} today.", "red", attrs=["bold"]))
            return False
            
        remaining = daily_limit - daily_count
        logger.info(colored(f"📊 {category.replace('_',' ').title()} Usage: {daily_count}/{daily_limit} (Safe: {remaining} more)", "cyan"))
        return True

    # Compatibility shims for legacy code
    def check_harvest_safety(self, handle: str) -> bool:
        # Map old 'harvest_pages' to 'people_searches' but actually pages are searches here
        return self.check_safety(handle, "people_searches", "harvest_pages")

    def check_enrich_safety(self, handle: str) -> bool:
        return self.check_safety(handle, "enrich_profiles", "enrich_profiles")
