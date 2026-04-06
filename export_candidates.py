import sys
import pandas as pd
import json
import logging
from linkedin.sessions.registry import get_session
from linkedin.db.profiles import get_all_profiles

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("Exporter")

def export_results(handle: str, output_file: str = "candidates_export.csv"):
    """
    Reads the local database for the given handle and exports all scraped candidate details to a CSV.
    """
    logger.info(f"Connecting to database for {handle}...")
    try:
        session = get_session(handle)
    except Exception as e:
        logger.error(f"Could not load session/database: {e}")
        return

    # Fetch all profiles from the DB
    # We'll need to do a raw query or use the existing helper if applicable
    # Since get_all_profiles isn't standard, let's use the db_session directly
    
    profiles = session.db_session.query(session.db.Profile).all()
    
    if not profiles:
        logger.warning("No profiles found in database yet. Run main.py first to scrape people!")
        return

    logger.info(f"Found {len(profiles)} profiles in database. Processing...")

    results = []
    
    for p in profiles:
        # p.profile contains the simplified data
        # p.data contains the raw massive JSON
        
        info = p.profile or {}
        
        # Flatten basic info
        row = {
            "LinkedIn ID": p.public_identifier,
            "Status": p.state,
            "Full Name": info.get("full_name", ""),
            "Headline": info.get("headline", ""),
            "Location": info.get("location_name", ""),
            "Summary": info.get("summary", ""),
            "Follower Count": info.get("follower_count", ""),
            "Occupation": info.get("occupation", ""),
            "Country": info.get("country_full_name", ""),
            "Profile URL": f"https://www.linkedin.com/in/{p.public_identifier}/"
        }

        # Extract Experience (Current Job)
        # Usually nested in 'experience' list
        # We take the first one as "Current"
        experience = info.get("experience", [])
        if experience and isinstance(experience, list):
            first_job = experience[0] if len(experience) > 0 else {}
            row["Current Company"] = first_job.get("company", "") or first_job.get("company_name", "")
            row["Current Title"] = first_job.get("title", "")
            row["Job Date Range"] = first_job.get("date_range", "")
        else:
            row["Current Company"] = ""
            row["Current Title"] = ""
            row["Job Date Range"] = ""

        results.append(row)

    # Save to CSV using Pandas
    df = pd.DataFrame(results)
    output_path = f"assets/{output_file}"
    df.to_csv(output_path, index=False)
    
    logger.info(f"✅ Exported {len(results)} fully detailed profiles to {output_path}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python export_candidates.py <handle> [output_filename]")
        print("Example: python export_candidates.py vishnu02.tech@gmail.com")
        sys.exit(1)

    handle = sys.argv[1]
    filename = sys.argv[2] if len(sys.argv) > 2 else "candidates_export.csv"
    
    export_results(handle, filename)
