import logging
import sys

from linkedin.csv_launcher import launch_connect_follow_up_campaign

logging.getLogger().handlers.clear()
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
)

import argparse

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("handle", nargs="?", default=None)
    parser.add_argument("--enrich-only", action="store_true")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--urls", nargs="*", help="Specific URLs to process")
    parser.add_argument("--note", help="Custom connection request note")
    parser.add_argument("--checkpoint", action="store_true", help="Run manual authentication checkpoint")
    args = parser.parse_args()
    
    if args.checkpoint:
        from linkedin.csv_launcher import checkpoint
        checkpoint(args.handle)
    else:
        launch_connect_follow_up_campaign(
            args.handle, 
            enrich_only=args.enrich_only, 
            limit=args.limit, 
            urls=args.urls,
            note=args.note
        )
