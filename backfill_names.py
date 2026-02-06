import csv
import os
from pathlib import Path
from urllib.parse import urlparse

ASSETS_DIR = Path("assets")
HARVEST_FILE = ASSETS_DIR / "inputs" / "harvested_urls.csv"

def backfill_names():
    if not HARVEST_FILE.exists():
        print("File not found.")
        return

    rows = []
    fieldnames = []
    with open(HARVEST_FILE, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        if "candidate_name" not in fieldnames:
            fieldnames.append("candidate_name")
        if "candidate_pic" not in fieldnames:
            fieldnames.append("candidate_pic")
        
        for row in reader:
            if not row.get("candidate_name") or row["candidate_name"] == "Harvested Profile":
                # Try to guess name from URL
                url = row.get("url", "")
                parsed = urlparse(url)
                path = parsed.path.strip("/")
                if path.startswith("in/"):
                    slug = path[3:].strip("/")
                    # Remove trailing alphanumeric garbage if present
                    name_parts = slug.split("-")
                    if len(name_parts) > 1 and len(name_parts[-1]) >= 8 and any(c.isdigit() for c in name_parts[-1]):
                        name_parts = name_parts[:-1]
                    
                    name = " ".join(name_parts).title()
                    row["candidate_name"] = name
            
            if "candidate_pic" not in row:
                row["candidate_pic"] = ""
            
            rows.append(row)

    with open(HARVEST_FILE, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Backfilled {len(rows)} entries.")

if __name__ == "__main__":
    backfill_names()
