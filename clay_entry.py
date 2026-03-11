# clay_entry.py
import argparse
import logging
import sys
from scooter_clay.sessions import ClaySessionManager
from scooter_clay.harvester import harvest_clay_leads
from termcolor import colored

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("clay_monitoring.log")
    ]
)
logger = logging.getLogger("ClayCLI")

def main():
    parser = argparse.ArgumentParser(description="ScooterAI Clay Harvester")
    parser.add_argument("--handle", required=True, help="Clay handle (email)")
    parser.add_argument("--url", required=True, help="Clay workbook/table URL")
    parser.add_argument("--limit", type=int, default=10, help="Max leads to capture")
    
    args = parser.parse_args()
    
    manager = ClaySessionManager()
    try:
        logger.info(colored(f"🚀 Initializing Clay Harvest for {args.handle}", "cyan", attrs=["bold"]))
        session = manager.get_session(args.handle)
        
        leads = harvest_clay_leads(session, args.url, limit=args.limit)
        
        if leads:
            logger.info(colored(f"✨ Clay Harvest Task Completed Successfully. Captured {len(leads)} leads.", "green", attrs=["bold"]))
        else:
            logger.warning(colored("⚠️ Task finished but no leads were captured. Check logs/screenshots.", "yellow"))
            
    except Exception as e:
        logger.error(colored(f"❌ Critical Failure during harvest: {e}", "red", attrs=["bold"]))
    finally:
        manager.close()

if __name__ == "__main__":
    main()
