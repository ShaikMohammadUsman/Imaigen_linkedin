# linkedin/actions/profile.py
import json
import logging
from pathlib import Path
from typing import Dict, Any

from linkedin.conf import FIXTURE_PROFILES_DIR
from linkedin.sessions.registry import get_session
from ..api.client import PlaywrightLinkedinAPI

logger = logging.getLogger(__name__)

# Global cache to avoid re-fetching company details multiple times during a single run
_COMPANY_CACHE = {}


def scrape_profile(handle: str, profile: dict):
    url = profile["url"]

    session = get_session(
        handle=handle,
    )

    # ── Existing enrichment logic (100% unchanged) ──
    session.ensure_browser()
    session.wait()

    api = PlaywrightLinkedinAPI(session=session)

    logger.info("Enriching profile → %s", url)
    enriched_profile, data = api.get_profile(profile_url=url)

    if enriched_profile:
        # Preserve input metadata (Role Tag, Company, etc) so it stays constant in the CRM
        for key in ["role_name", "company_name", "app_link", "location", "compensation", "job_id"]:
             if key in profile and key not in enriched_profile:
                 enriched_profile[key] = profile[key]

        # --- COMPANY ENRICHMENT ---
        # Fetch detailed info for companies in their work history
        if "positions" in enriched_profile:
            logger.info(f"Fetching company details for {len(enriched_profile['positions'])} positions...")
            
            for pos in enriched_profile["positions"]:
                urn = pos.get("company_urn")
                if urn:
                    # Check cache first
                    if urn in _COMPANY_CACHE:
                        pos["company_details"] = _COMPANY_CACHE[urn]
                    else:
                        # Fetch from API
                        try:
                            # Small randomized delay for stealth before each company API call
                            session.wait() 
                            
                            # Extract ID just in case
                            company_id = urn.split(':')[-1]
                            details = api.get_company(company_id)
                            if details:
                                pos["company_details"] = details
                                _COMPANY_CACHE[urn] = details
                                logger.debug(f"  + Enriched Company: {details.get('name')}")
                        except Exception as e:
                            logger.warning(f"  - Failed to enrich company {urn}: {e}")
                            
            logger.info(f"Enriched {len(_COMPANY_CACHE)} unique companies globally.")

    logger.info("Profile enriched – %s", enriched_profile.get("public_identifier")) if enriched_profile else None

    return enriched_profile, data


def _save_profile_to_fixture(enriched_profile: Dict[str, Any], path: str | Path) -> None:
    """Utility to save enriched profile as test fixture."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(enriched_profile, f, indent=2, ensure_ascii=False, default=str)
    logger.info("Enriched profile saved to fixture → %s", path)


# python -m linkedin.actions.profile
if __name__ == "__main__":
    import sys
    from linkedin.campaigns.connect_follow_up import INPUT_CSV_PATH

    FIXTURE_PATH = FIXTURE_PROFILES_DIR / "linkedin_profile.json"

    logging.getLogger().handlers.clear()
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s │ %(levelname)-8s │ %(message)s',
        datefmt="%H:%M:%S",
    )

    if len(sys.argv) != 2:
        print("Usage: python -m linkedin.actions.profile <handle>")
        sys.exit(1)

    handle = sys.argv[1]

    test_profile = {
        "url": "https://www.linkedin.com/in/me/",
    }

    profile, data = scrape_profile(handle, test_profile)
    from pprint import pprint

    pprint(profile)
    # _save_profile_to_fixture(data, FIXTURE_PATH)
