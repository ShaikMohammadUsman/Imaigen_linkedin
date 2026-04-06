# apollo_entry.py
import argparse
import logging
import sys
import yaml
from scooter_apollo.sessions import ApolloSessionManager
from scooter_apollo.harvester import harvest_apollo_leads
from termcolor import colored

# Configure Logging to match UI expectations
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("ApolloCLI")

def main():
    parser = argparse.ArgumentParser(description="Scooter Apollo CLI")
    parser.add_argument("--handle", required=True, help="Account handle")
    parser.add_argument("--search-url", required=True, help="Apollo search URL")
    parser.add_argument("--pages", type=int, default=1, help="Number of pages")
    parser.add_argument("--limit", type=int, default=50, help="Max unlocks")
    
    args = parser.parse_args()
    
    logger.info(colored(f"🚀 Initializing Apollo Harvest for {args.handle}", "cyan", attrs=["bold"]))
    
    # Load Credentials
    try:
        with open("assets/apollo.secrets.yaml", "r") as f:
            secrets = yaml.safe_load(f)
            account = secrets.get("accounts", {}).get(args.handle)
            if not account:
                logger.error(f"No credentials found for {args.handle} in assets/apollo.secrets.yaml")
                sys.exit(1)
    except FileNotFoundError:
        logger.error("Missing assets/apollo.secrets.yaml")
        sys.exit(1)

    # Start Session
    manager = ApolloSessionManager()
    session = manager.get_session(
        email=account.get("email"),
        password=account.get("password"),
        login_method=account.get("login_method", "google")
    )
    
    if not session:
        logger.error(colored("❌ Failed to establish Apollo Session.", "red"))
        sys.exit(1)
        
    # Start SQL Job Tracking
    from linkedin.db.engine import Database
    from linkedin.db.jobs import create_job, update_job_progress, end_job
    
    db = Database.from_handle(args.handle)
    db_session = db.get_session()
    job_id = create_job(db_session, args.handle, "apollo_harvest", expected_limit=args.limit)
        
    try:
        # Run Harvester
        leads = harvest_apollo_leads(session, args.search_url, pages=args.pages)
        if leads:
             update_job_progress(db_session, job_id, len(leads))
        end_job(db_session, job_id, "completed")
        logger.info(colored(f"✨ Apollo Harvest Task Completed Successfully. Captured {len(leads) if leads else 0}.", "green", attrs=["bold"]))
    except Exception as e:
        end_job(db_session, job_id, "failed", str(e))
        logger.error(f"Critical Failure during harvest: {e}")
    finally:
        db.Session.remove()
        manager.close()

if __name__ == "__main__":
    main()
