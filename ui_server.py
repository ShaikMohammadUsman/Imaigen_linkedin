import asyncio
import logging
import os
import subprocess
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, BackgroundTasks, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse
import pandas as pd
import uvicorn
import signal
import sys

# Setup
app = FastAPI(title="OpenOutreach API")
BASE_DIR = Path(__file__).parent.absolute()
ASSETS_DIR = BASE_DIR / "assets"
INPUTS_DIR = ASSETS_DIR / "inputs"
# Using the auto-updated detailed CSV
DB_EXPORT_FILE = ASSETS_DIR / "candidates_detailed.csv"

# Global state for process management
current_process = None
log_queue = asyncio.Queue()

# CORS for local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files (Frontend)
app.mount("/static", StaticFiles(directory="ui/static"), name="static")

async def read_stream(stream):
    """Read stdout/stderr from subprocess and pushing to log queue."""
    while True:
        line = await stream.readline()
        if line:
            text = line.decode('utf-8').strip()
            print(f"[BOT] {text}")
            await log_queue.put(text)
        else:
            break

@app.get("/", response_class=HTMLResponse)
async def read_index():
    with open("ui/static/index.html") as f:
        return f.read()

@app.get("/api/logs")
async def sse_logs(request: Request):
    """Server-Sent Events context for logging."""
    async def event_generator():
        while True:
            if await request.is_disconnected():
                break
            # Get log message (wait for it)
            msg = await log_queue.get()
            yield {"data": msg}
    return EventSourceResponse(event_generator())

@app.post("/api/harvest")
async def start_harvest(
    handle: str, 
    search_url: str, 
    job_id: str = "", 
    role_name: str = "", 
    company_name: str = "",
    app_link: str = "",
    location: str = "",
    compensation: str = "",
    start_page: int = 1, 
    pages: int = 5
):
    global current_process
    if current_process and current_process.returncode is None:
        return JSONResponse({"status": "error", "message": "A process is already running."}, status_code=400)
    
    cmd = [
        "./venv/bin/python", "-u", "harvest_search.py", 
        handle, search_url, str(start_page), str(pages), job_id, role_name, company_name, app_link, location, compensation
    ]
    
    current_process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(BASE_DIR)
    )
    
    # Start readers in background
    asyncio.create_task(read_stream(current_process.stdout))
    asyncio.create_task(read_stream(current_process.stderr))
    
    return {"status": "started", "pid": current_process.pid}

@app.get("/api/scraped_data")
def get_scraped_data():
    csv_path = ASSETS_DIR / "inputs" / "harvested_urls.csv"
    if not csv_path.exists():
        return {"data": []}
    
    data = []
    try:
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                 data.append(row)
    except Exception as e:
        return {"data": [], "error": str(e)}
        
    return {"data": data}

from fastapi.responses import FileResponse

@app.get("/api/download_csv")
def download_csv():
    file_path = ASSETS_DIR / "inputs" / "harvested_urls.csv"
    if not file_path.exists():
        return JSONResponse({"status": "error", "message": "File not found"}, status_code=404)
        
    return FileResponse(file_path, media_type='text/csv', filename="harvested_candidates.csv")

# Consolidated with the one below at 434

@app.post("/api/campaign")
async def start_campaign(req: Request):
    data = await req.json()
    handle = data.get("handle")
    enrich_only = data.get("enrich_only", False)
    limit = data.get("limit", 20)
    urls = data.get("urls")

    global current_process
    if current_process and current_process.returncode is None:
        return JSONResponse({"status": "error", "message": "A process is already running."}, status_code=400)
    
    cmd = ["./venv/bin/python", "-u", "main.py", handle]
    if enrich_only:
        cmd.append("--enrich-only")
        
    cmd.append("--limit")
    cmd.append(str(limit))

    if urls and isinstance(urls, list):
        cmd.append("--urls")
        cmd.extend(urls)
    
    current_process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(BASE_DIR)
    )
    
    msg = f"Starting campaign for @{handle} (Enrich: {enrich_only}, Limit: {limit})"
    print(f"[SERVER] {msg}")
    await log_queue.put(msg)
    
    asyncio.create_task(read_stream(current_process.stdout))
    asyncio.create_task(read_stream(current_process.stderr))
    
    return {"status": "started", "pid": current_process.pid}

@app.post("/api/stop")
async def stop_process():
    global current_process
    if current_process:
        try:
            current_process.terminate()
            await current_process.wait()
            current_process = None
            return {"status": "stopped"}
        except Exception as e:
            return {"status": "error", "message": str(e)}
    return {"status": "no_process"}

@app.get("/api/results")
def get_results(handle: str = None, refresh: bool = False):
    import csv
    from linkedin.db.engine import Database
    from linkedin.db.models import Profile
    from linkedin.db.profiles import url_to_public_id

    # 1. Load Master Queue (CSV) - These are our "Constant" results
    queue_records = []
    if HARVEST_FILE.exists():
        try:
            with open(HARVEST_FILE, "r", encoding="utf-8") as f:
                queue_records = list(csv.DictReader(f))
        except Exception as e:
            print(f"Error reading harvest file: {e}")

    # 2. Get Enrichment Data from DB for the current handle
    db_profiles_map = {}
    if handle and handle != "undefined":
        try:
            db_wrapper = Database.from_handle(handle)
            session = db_wrapper.get_session()
            try:
                # Fetch all profiles that have some scraped data
                profiles = session.query(Profile).filter(Profile.profile.isnot(None)).all()
                for p in profiles:
                    db_profiles_map[p.public_identifier] = p
            finally:
                session.close()
                db_wrapper.Session.remove()
        except Exception as e:
            print(f"Results DB Error for {handle}: {e}")

    final_results = []
    processed_pids = set()

    # 3. Merge CSV (Primary) with DB (Enrichment)
    from linkedin.api.voyager import parse_linkedin_voyager_response
    for row in queue_records:
        url = row.get("url", "")
        if not url: continue
        
        pid = None
        try: pid = url_to_public_id(url)
        except: pass
        
        # Default data from CSV (The "Constant" part)
        record = {
            "Full Name": row.get("candidate_name") or "Harvested Profile",
            "Role": row.get("role_name") or "Generic",
            "Headline": "Pending enrichment...",
            "Current Company": row.get("company_name") or "-",
            "Location": row.get("location") or "-",
            "Email": "",
            "About": "",
            "Status": "HARVESTED",
            "URL": url,
            "Picture": row.get("candidate_pic") or ""
        }

        # Override with DB data if enriched
        if pid and pid in db_profiles_map:
            p = db_profiles_map[pid]
            data = p.profile
            raw_data = p.data
            
            # Fallback for older stringified data
            if isinstance(data, str):
                import json
                try: data = json.loads(data)
                except: data = {}
            if isinstance(raw_data, str):
                import json
                try: raw_data = json.loads(raw_data)
                except: raw_data = {}
            
            # HEAL old records by re-parsing raw Voyager data
            if raw_data and (not data or "positions" not in data or "skills" not in data):
                try:
                    healed_data = parse_linkedin_voyager_response(raw_data, public_identifier=pid)
                    if healed_data:
                        data = healed_data
                except Exception as e:
                    print(f"Heal failed for {pid}: {e}")
            
            exp = data.get("positions", [])
            exp_list = []
            company = record["Current Company"]
            for i, job in enumerate(exp):
                title = job.get('title', 'Position')
                comp = job.get('company_name', '') or job.get('company', '')
                if i == 0 and comp: company = comp
                
                # Format Dates
                dr = job.get('date_range') or {}
                start = dr.get('start') or {}
                end = dr.get('end') or {}
                start_str = f"{start.get('month', '') or ''}/{start.get('year', '') or ''}".strip("/")
                end_str = f"{end.get('month', '') or ''}/{end.get('year', '') or ''}".strip("/") or "Present"
                dates = f"{start_str} - {end_str}" if start_str else ""
                
                exp_list.append({
                    "title": title,
                    "company": comp,
                    "dates": dates
                })
            
            skills = data.get("skills", [])
            if isinstance(skills, list):
                skills_str = ", ".join([str(s) for s in skills[:12]])
            else:
                skills_str = ""
            
            record.update({
                "Full Name": data.get("full_name") or data.get("name") or record["Full Name"],
                "Headline": data.get("headline") or data.get("occupation") or record["Headline"],
                "Current Company": company,
                "Experience": exp_list,
                "Skills": skills_str,
                "Location": data.get("location_name") or data.get("city") or record["Location"],
                "Email": data.get("email") or "",
                "Phone": data.get("phone") or "",
                "About": data.get("summary") or data.get("about") or "",
                "Status": p.state.upper(),
                "Picture": data.get("profile_picture") or "",
                "Last Message": p.last_message,
                "Last Message At": p.last_message_at.isoformat() if p.last_message_at else None,
                "Last Received Message": p.last_received_message,
                "Last Received At": p.last_received_at.isoformat() if p.last_received_at else None
            })
            processed_pids.add(pid)

        final_results.append(record)

    # 4. Add profiles from DB that are NOT in current CSV (Just in case)
    for pid, p in db_profiles_map.items():
        if pid not in processed_pids:
            data = p.profile
            raw_data = p.data
            
            if isinstance(data, str):
                import json
                try: data = json.loads(data)
                except: data = {}
            if isinstance(raw_data, str):
                import json
                try: raw_data = json.loads(raw_data)
                except: raw_data = {}

            if raw_data and (not data or "positions" not in data or "skills" not in data):
                try:
                    healed = parse_linkedin_voyager_response(raw_data, public_identifier=pid)
                    if healed: data = healed
                except: pass
                
            exp = data.get("positions", [])
            exp_list = []
            company = ""
            for i, job in enumerate(exp):
                title = job.get('title', 'Position')
                comp = job.get('company_name', '') or job.get('company', '')
                if i == 0: company = comp
                dr = job.get('date_range') or {}
                start_str = f"{dr.get('start', {}).get('month', '')}/{dr.get('start', {}).get('year', '')}".strip("/")
                end_str = f"{dr.get('end', {}).get('month', '')}/{dr.get('end', {}).get('year', '')}".strip("/") or "Present"
                exp_list.append({"title": title, "company": comp, "dates": f"{start_str} - {end_str}" if start_str else ""})
            
            skills = data.get("skills", [])
            skills_str = ", ".join([str(s) for s in skills[:12]]) if isinstance(skills, list) else ""
                
            final_results.append({
                "Full Name": data.get("full_name") or data.get("name") or "Legacy Candidate",
                "Role": data.get("role_name") or "Old Campaign",
                "Headline": data.get("headline") or "-",
                "Current Company": company,
                "Experience": exp_list,
                "Skills": skills_str,
                "Location": data.get("location_name") or data.get("city") or "-",
                "Email": data.get("email") or "",
                "Phone": data.get("phone") or "",
                "About": data.get("summary") or data.get("about") or "",
                "Status": p.state.upper(),
                "URL": f"https://www.linkedin.com/in/{pid}"
            })

    # 5. Newest first
    final_results.reverse()
    
    return {
        "data": final_results,
        "stats": {
            "total": len(final_results),
            "breakdown": {"total": len(final_results)}
        }
    }

# --- ROLES API ---
ROLES_FILE = ASSETS_DIR / "roles.json"

@app.get("/api/roles")
def get_roles():
    if not ROLES_FILE.exists():
        return []
    import json
    try:
        with open(ROLES_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return []

@app.post("/api/roles")
async def add_role(request: Request):
    import json
    new_role = await request.json()
    
    roles = []
    if ROLES_FILE.exists():
        with open(ROLES_FILE, "r") as f:
            try:
                roles = json.load(f)
            except: pass
            
    roles.append(new_role)
    
    with open(ROLES_FILE, "w") as f:
        json.dump(roles, f, indent=2)
    return {"status": "ok"}

@app.delete("/api/roles/{idx}")
def delete_role(idx: int):
    import json
    if not ROLES_FILE.exists(): return
    
    with open(ROLES_FILE, "r") as f:
        roles = json.load(f)
        
    if 0 <= idx < len(roles):
        roles.pop(idx)
        with open(ROLES_FILE, "w") as f:
            json.dump(roles, f, indent=2)
            
    return {"status": "ok"}

# --- QUEUE MANAGEMENT (Review before Campaign) ---
HARVEST_FILE = ASSETS_DIR / "inputs" / "harvested_urls.csv"

from linkedin.db.models import Profile
from linkedin.db.engine import Database
from linkedin.db.profiles import url_to_public_id

from linkedin.usage_tracker import UsageTracker, HARVEST_PAGE_LIMIT

@app.get("/api/usage")
def get_usage(handle: str):
    from linkedin.usage_tracker import UsageTracker, ENRICH_PROFILE_LIMIT, HARVEST_PAGE_LIMIT
    
    if not handle or handle == "undefined":
        return {
            "count": 0,
            "limit": ENRICH_PROFILE_LIMIT,
            "harvest_pages": 0,
            "harvest_limit": HARVEST_PAGE_LIMIT,
            "is_safe": True,
            "remaining": ENRICH_PROFILE_LIMIT
        }
        
    tracker = UsageTracker(ASSETS_DIR)
    enrich_count = tracker.get_todays_count(handle, "enrich_profiles")
    harvest_count = tracker.get_todays_count(handle, "harvest_pages")
    
    return {
        "count": enrich_count,
        "limit": ENRICH_PROFILE_LIMIT,
        "harvest_pages": harvest_count,
        "harvest_limit": HARVEST_PAGE_LIMIT,
        "is_safe": enrich_count < ENRICH_PROFILE_LIMIT,
        "remaining": max(0, ENRICH_PROFILE_LIMIT - enrich_count)
    }
    
@app.get("/api/queue")
def get_queue(handle: str = None):
    if not HARVEST_FILE.exists():
        return []
    
    import csv
    queue_data = []
    try:
        with open(HARVEST_FILE, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            queue_data = list(reader)
    except Exception:
        pass
    
    # Enrich with DB Status only if handle is provided
    if not handle:
        return queue_data

    try:
        # We need to suppress logs or it might be noisy
        db_wrapper = Database.from_handle(handle)
        session = db_wrapper.get_session()
        
        try:
            for row in queue_data:
                url = row.get("url", "")
                row["status"] = "pending" # Default
                row["email"] = ""
                row["current_company"] = ""
                
                try:
                    pid = url_to_public_id(url)
                    profile = session.query(Profile).filter(Profile.public_identifier == pid).first()
                    if profile:
                        row["status"] = profile.state
                        
                        # Merge profile details if enriched
                        if profile.profile:
                            p_data = profile.profile
                            # Handle potential stringified JSON
                            if isinstance(p_data, str):
                                import json
                                try: p_data = json.loads(p_data)
                                except: p_data = {}
                                
                            row["email"] = p_data.get("email", "")
                            
                            exp = p_data.get("experience", [])
                            if exp and isinstance(exp, list) and len(exp) > 0:
                                 row["current_company"] = exp[0].get("company", "") or exp[0].get("company_name", "")
                            
                            # Also pull name and pic if we have them in the DB but not in the CSV row
                            if not row.get("candidate_name") or row["candidate_name"] == "Harvested Profile":
                                row["candidate_name"] = p_data.get("full_name") or p_data.get("name")
                            if not row.get("candidate_pic"):
                                row["candidate_pic"] = p_data.get("profile_picture") or ""
                except:
                    pass
        finally:
            session.close()
            db_wrapper.Session.remove()
    except Exception as e:
        print(f"Error accessing DB for handle {handle}: {e}")
        pass
        
    return queue_data

@app.post("/api/queue")
async def save_queue(request: Request):
    """Overwrite the queue with new list (after user edits/deletions)"""
    new_data = await request.json()
    import csv
    
    # Ensure directory exists (it should, but safety first)
    HARVEST_FILE.parent.mkdir(parents=True, exist_ok=True)
    
    fieldnames = ["url", "job_id", "role_name", "company_name", "app_link", "location", "compensation", "candidate_name", "candidate_pic"]
    
    with open(HARVEST_FILE, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(new_data)
        
    return {"status": "saved", "count": len(new_data)}


@app.post("/api/check_replies")
async def check_replies_now(handle: str):
    """Manually trigger reply checking for a specific account."""
    global current_process
    if current_process and current_process.returncode is None:
        return JSONResponse({"status": "error", "message": "Another process is running."}, status_code=400)
    
    cmd = ["./venv/bin/python", "-u", "check_replies.py"]
    
    current_process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(BASE_DIR)
    )
    
    msg = f"Checking for new replies..."
    print(f"[SERVER] {msg}")
    await log_queue.put(msg)
    
    asyncio.create_task(read_stream(current_process.stdout))
    asyncio.create_task(read_stream(current_process.stderr))
    
    return {"status": "started", "pid": current_process.pid}


if __name__ == "__main__":
    uvicorn.run("ui_server:app", host="0.0.0.0", port=8000, reload=True)
