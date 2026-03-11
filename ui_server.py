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

class Broadcaster:
    def __init__(self):
        self.clients = set()

    async def subscribe(self):
        queue = asyncio.Queue()
        self.clients.add(queue)
        try:
            while True:
                msg = await queue.get()
                yield {"data": msg}
        finally:
            self.clients.remove(queue)

    async def put(self, msg):
        for queue in list(self.clients):
            await queue.put(msg)

broadcaster = Broadcaster()

async def push_log(msg):
    print(f"[SERVER] {msg}")
    await broadcaster.put(msg)

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
    """Read stdout/stderr from subprocess and pushing to broadcaster."""
    while True:
        line = await stream.readline()
        if line:
            text = line.decode('utf-8').strip()
            print(f"[BOT] {text}")
            await broadcaster.put(text)
        else:
            break

@app.get("/", response_class=HTMLResponse)
async def read_index():
    with open("ui/static/index.html") as f:
        return f.read()

@app.get("/api/logs")
async def sse_logs(request: Request):
    """Server-Sent Events context for logging."""
    print(f"🔌 [SERVER] New log stream connection established from {request.client.host}")
    
    async def event_generator():
        queue = asyncio.Queue()
        broadcaster.clients.add(queue)
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield {"data": msg}
                except asyncio.TimeoutError:
                    yield {"data": "ping"}
        finally:
            broadcaster.clients.remove(queue)

    return EventSourceResponse(event_generator())

@app.get("/api/accounts")
def get_accounts():
    from linkedin.conf import list_active_accounts
    return list_active_accounts()

@app.post("/api/reset_health")
def reset_health(handle: str):
    from linkedin.usage_tracker import UsageTracker
    from linkedin.conf import ASSETS_DIR
    tracker = UsageTracker(ASSETS_DIR)
    success = tracker.reset_health(handle)
    return {"status": "success" if success else "failed"}

@app.post("/api/harvest")
async def start_harvest(request: Request):
    global current_process
    if current_process and current_process.returncode is None:
        return JSONResponse({"status": "error", "message": "A process is already running."}, status_code=400)
    
    data = await request.json()
    handle = data.get("handle")
    # Handle mapping from frontend names to backend expected names
    search_url = data.get("url") or data.get("search_url")
    job_id = data.get("job_id", "")
    role_name = data.get("role_name", "")
    company_name = data.get("company_name") or data.get("hiring_company", "")
    app_link = data.get("app_link") or data.get("job_link", "")
    location = data.get("location", "")
    compensation = data.get("compensation", "")
    start_page = data.get("start_page", 1)
    pages = data.get("pages", 5)

    if not handle or not search_url:
        return JSONResponse({"status": "error", "message": "Handle and Search URL are required."}, status_code=422)

    cmd = [
        sys.executable, "-u", "harvest_search.py", 
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

@app.post("/api/apollo_harvest")
async def start_apollo_harvest(request: Request):
    global current_process
    if current_process and current_process.returncode is None:
        return JSONResponse({"status": "error", "message": "A process is already running."}, status_code=400)
    
    data = await request.json()
    handle = data.get("handle")
    search_url = data.get("url") or data.get("search_url")
    limit = data.get("limit", 50)
    pages = data.get("pages", 1)

    if not handle or not search_url:
        return JSONResponse({"status": "error", "message": "Handle and Search URL are required."}, status_code=422)

    cmd = [
        sys.executable, "-u", "apollo_entry.py",
        "--handle", handle,
        "--search-url", search_url,
        "--limit", str(limit),
        "--pages", str(pages)
    ]
    
    current_process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(BASE_DIR)
    )
    
    msg = f"🛰️ Launching Apollo Source: {search_url} (Limit: {limit})"
    print(f"[SERVER] {msg}")
    await push_log(msg)
    
    asyncio.create_task(read_stream(current_process.stdout))
    asyncio.create_task(read_stream(current_process.stderr))
    
    return {"status": "started", "pid": current_process.pid}

@app.post("/api/clay_harvest")
async def start_clay_harvest(request: Request):
    global current_process
    if current_process and current_process.returncode is None:
        return JSONResponse({"status": "error", "message": "A process is already running."}, status_code=400)
    
    data = await request.json()
    handle = data.get("handle")
    url = data.get("url")
    limit = data.get("limit", 50)

    if not handle or not url:
        return JSONResponse({"status": "error", "message": "Handle and URL are required."}, status_code=422)

    cmd = [
        sys.executable, "-u", "clay_entry.py",
        "--handle", handle,
        "--url", url,
        "--limit", str(limit)
    ]
    
    current_process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(BASE_DIR)
    )
    
    msg = f"💎 Launching Clay Workbook: {url} (Limit: {limit})"
    print(f"[SERVER] {msg}")
    await push_log(msg)
    
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

@app.get("/api/download_detailed_csv")
def download_detailed_csv():
    if not DB_EXPORT_FILE.exists():
        return JSONResponse({"status": "error", "message": "Detailed report not found. Run enrichment first."}, status_code=404)
        
    return FileResponse(DB_EXPORT_FILE, media_type='text/csv', filename="detailed_candidates_report.csv")

# Consolidated with the one below at 434

@app.post("/api/campaign")
async def start_campaign(req: Request):
    data = await req.json()
    handle = data.get("handle")
    enrich_only = data.get("enrich_only", False)
    limit = data.get("limit", 20)
    urls = data.get("urls")
    note = data.get("note")

    global current_process
    if current_process and current_process.returncode is None:
        return JSONResponse({"status": "error", "message": "A process is already running."}, status_code=400)
    
    cmd = [sys.executable, "-u", "main.py", handle]
    if enrich_only:
        cmd.append("--enrich-only")
        
    cmd.append("--limit")
    cmd.append(str(limit))

    if urls and isinstance(urls, list):
        cmd.append("--urls")
        cmd.extend(urls)
        
    if note:
        cmd.append("--note")
        cmd.append(note)
    
    current_process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(BASE_DIR)
    )
    
    msg = f"Starting campaign for @{handle} (Enrich: {enrich_only}, Limit: {limit})"
    print(f"[SERVER] {msg}")
    await push_log(msg)
    
    asyncio.create_task(read_stream(current_process.stdout))
    asyncio.create_task(read_stream(current_process.stderr))
    
    return {"status": "started", "pid": current_process.pid}

@app.post("/api/checkpoint")
async def start_checkpoint(req: Request):
    data = await req.json()
    handle = data.get("handle")
    if not handle:
        return JSONResponse({"status": "error", "message": "No handle provided"}, status_code=400)
        
    global current_process
    if current_process and current_process.returncode is None:
        return JSONResponse({"status": "error", "message": "Another process is already running."}, status_code=400)
        
    cmd = [sys.executable, "-u", "-m", "linkedin.navigation.login", handle, "--checkpoint"]
    
    current_process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(BASE_DIR)
    )
    
    msg = f"🛡️ Manual Checkpoint Started for @{handle}"
    await push_log(msg)
    
    asyncio.create_task(read_stream(current_process.stdout))
    asyncio.create_task(read_stream(current_process.stderr))
    
    return {"status": "started", "pid": current_process.pid}

@app.get("/api/process_status")
def get_process_status():
    global current_process
    if current_process and current_process.returncode is None:
        return {"busy": True, "pid": current_process.pid}
    return {"busy": False}

@app.post("/api/stop")
async def stop_process():
    global current_process
    
    msg = "🛑 [SERVER] Stop request received. Terminating processes..."
    print(msg)
    await push_log(msg)
    
    # 1. Primary: Stop the tracked process
    if current_process:
        try:
            # Try gentle terminate first
            current_process.terminate()
            try:
                # Wait up to 3 seconds for it to exit
                await asyncio.wait_for(current_process.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                # Still alive? Use the hammer.
                print("⚠️ [SERVER] Process did not exit gracefully, using SIGKILL.")
                current_process.kill()
                await current_process.wait()
            
            current_process = None
        except Exception as e:
            print(f"❌ [SERVER] Error stopping process handle: {e}")

    # 2. Secondary/Fallback: Kill any orphaned bot processes 
    # (Happens if server was reloaded while a bot was running)
    try:
        import psutil
        count = 0
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                # Look for our script names
                cmd = " ".join(proc.info.get('cmdline') or [])
                if any(x in cmd for x in ["harvest_search.py", "main.py", "check_replies.py"]):
                    # Don't kill the server itself (obviously)
                    if proc.pid != os.getpid():
                        proc.kill()
                        count += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        if count > 0:
            msg = f"🧹 [SERVER] Cleaned up {count} orphaned bot processes."
            print(msg)
            await push_log(msg)
    except ImportError:
        # psutil not installed, fallback to basic pkill command
        os.system("pkill -9 -f 'harvest_search.py|main.py|check_replies.py'")
        print("🧹 [SERVER] Fallback cleanup (pkill) executed.")

    return {"status": "stopped"}

@app.get("/api/results")
def get_results(handle: str = None, refresh: bool = False):
    try:
        import csv
        import traceback
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
                # traceback.print_exc()

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
            
            # Detect Source
            source = "LinkedIn"
            role_lower = (row.get("role_name") or "").lower()
            if "clay" in role_lower: source = "Clay"
            elif "apollo" in role_lower: source = "Apollo"

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
                "LinkedIn URL": url, # Explicit field for sorting/display
                "Source": source,
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
                
                if not data: data = {}
                exp = data.get("positions", [])
                exp_list = []
                company = record["Current Company"]
                for i, job in enumerate(exp):
                    if not job: continue
                    title = job.get('title', 'Position')
                    comp = job.get('company_name', '') or job.get('company', '')
                    if i == 0 and comp: company = comp
                    
                    # Format Dates
                    dr = job.get('date_range') or {}
                    if not dr: dr = {}
                    start = dr.get('start') or {}
                    end = dr.get('end') or {}
                    
                    start_val = (start or {})
                    end_val = (end or {})
                    
                    start_str = f"{start_val.get('month', '') or ''}/{start_val.get('year', '') or ''}".strip("/")
                    end_str = f"{end_val.get('month', '') or ''}/{end_val.get('year', '') or ''}".strip("/") or "Present"
                    dates = f"{start_str} - {end_str}" if start_str else ""
                    
                    details = job.get('company_details') or {}
                    exp_list.append({
                        "title": title,
                        "company": comp,
                        "dates": dates,
                        "company_description": details.get('description') or '',
                        "company_website": details.get('url') or '',
                        "company_industry": details.get('industry') or '',
                        "company_size": details.get('employee_count') or '',
                        "company_headquarters": details.get('headquarters') or '',
                        "company_specialties": details.get('specialties') or []
                    })
                
                skills = data.get("skills", [])
                if isinstance(skills, list):
                    skills_str = ", ".join([str(s) for s in skills[:12] if s])
                else:
                    skills_str = ""
                
                record.update({
                    "Full Name": data.get("full_name") or data.get("name") or record["Full Name"],
                    "Headline": data.get("headline") or data.get("occupation") or record["Headline"],
                    "Current Company": company,
                    "Experience": exp_list,
                    "Skills": skills_str,
                    "Location": data.get("location_name") or data.get("city") or record["Location"],
                    "State": data.get("state") or "",
                    "Country": data.get("country") or "",
                    "Certifications": data.get("certifications", []),
                    "Projects": data.get("projects", []),
                    "Education": data.get("educations", []),
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
                    
                if not data: data = {}
                exp = data.get("positions", [])
                exp_list = []
                company = ""
                for i, job in enumerate(exp):
                    if not job: continue
                    title = job.get('title', 'Position')
                    comp = job.get('company_name', '') or job.get('company', '')
                    if i == 0: company = comp
                    dr = job.get('date_range') or {}
                    if not dr: dr = {}
                    
                    start = dr.get('start') or {}
                    end = dr.get('end') or {}
                    
                    start_val = (start or {})
                    end_val = (end or {})
                    
                    start_str = f"{start_val.get('month', '') or ''}/{start_val.get('year', '') or ''}".strip("/")
                    end_str = f"{end_val.get('month', '') or ''}/{end_val.get('year', '') or ''}".strip("/") or "Present"
                    
                    details = job.get('company_details') or {}
                    exp_list.append({
                        "title": title, 
                        "company": comp, 
                        "dates": f"{start_str} - {end_str}" if start_str else "",
                        "company_description": details.get('description') or '',
                        "company_website": details.get('url') or '',
                        "company_industry": details.get('industry') or '',
                        "company_size": details.get('employee_count') or '',
                        "company_headquarters": details.get('headquarters') or '',
                    })
                
                skills = data.get("skills", [])
                skills_str = ", ".join([str(s) for s in skills[:12] if s]) if isinstance(skills, list) else ""
                    
                # Detect Source for legacy
                source = "LinkedIn"
                role_lower = (data.get("role_name") or "").lower()
                if "clay" in role_lower: source = "Clay"
                elif "apollo" in role_lower: source = "Apollo"

                final_results.append({
                    "Full Name": data.get("full_name") or data.get("name") or "Legacy Candidate",
                    "Role": data.get("role_name") or "Old Campaign",
                    "Headline": data.get("headline") or "-",
                    "Current Company": company,
                    "Experience": exp_list,
                    "Skills": skills_str,
                    "Location": data.get("location_name") or data.get("city") or "-",
                    "State": data.get("state") or "",
                    "Country": data.get("country") or "",
                    "Certifications": data.get("certifications", []),
                    "Projects": data.get("projects", []),
                    "Education": data.get("educations", []),
                    "Email": data.get("email") or "",
                    "Phone": data.get("phone") or "",
                    "About": data.get("summary") or data.get("about") or "",
                    "Status": p.state.upper(),
                    "URL": f"https://www.linkedin.com/in/{pid}",
                    "LinkedIn URL": f"https://www.linkedin.com/in/{pid}",
                    "Source": source,
                    "Picture": data.get("profile_picture") or "",
                    "Last Message": p.last_message,
                    "Last Message At": p.last_message_at.isoformat() if p.last_message_at else None,
                    "Last Received Message": p.last_received_message,
                    "Last Received At": p.last_received_at.isoformat() if p.last_received_at else None
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
    except Exception as e:
        print(f"CRITICAL ERROR in get_results: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"message": str(e), "data": []})

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
    from linkedin.usage_tracker import UsageTracker, SAFETY_CONFIG
    
    tracker = UsageTracker(ASSETS_DIR)
    
    # Categories to monitor
    categories = ["enrich_profiles", "harvested_cards", "people_searches"]
    results = {}
    is_safe_all = True
    
    if not handle or handle == "undefined":
        # Return sensible defaults
        for cat in categories:
            results[cat] = {
                "session": {"count": 0, "limit": 0},
                "daily": {"count": 0, "limit": 0},
                "weekly": {"count": 0, "limit": 0},
                "monthly": {"count": 0, "limit": 0}
            }
        return results
    
    for cat in categories:
        config = SAFETY_CONFIG.get(cat, {})
        
        # Calculate limits
        s_limit = tracker.get_session_limit(handle, cat)
        d_limit = tracker.get_dynamic_daily_limit(handle, cat)
        w_limit = config.get("weekly_target_range", (0, 0))[1]
        m_limit = config.get("monthly_target_range", (0, 0))[1]
        
        # Get counts (Session is always 0 at API start, 
        # but for simplicity we can track it per-request if we want? 
        # Actually session count here means 'in current run', but we don't have a persistent session counter in API.
        # However, we can track 'since UI load'? No, better to just show the session LIMIT 
        # and maybe the UI handles the increment? 
        # Actually, let's keep session count as 0 for now as it resets frequently.
        counts = {
            "session": {"count": 0, "limit": s_limit}, 
            "daily": {"count": tracker.get_count(handle, cat, "daily"), "limit": d_limit},
            "weekly": {"count": tracker.get_count(handle, cat, "weekly"), "limit": w_limit},
            "monthly": {"count": tracker.get_count(handle, cat, "monthly"), "limit": m_limit}
        }
        
        # Safety Check
        is_safe = counts["daily"]["count"] < d_limit and counts["monthly"]["count"] < m_limit
        if not is_safe: is_safe_all = False
        
        results[cat] = counts
        results[cat]["is_safe"] = is_safe

    # 🟢 Add Account Metadata
    stats = tracker._load_stats()
    metadata = stats.get(handle, {}).get("metadata", {})
    first_seen = metadata.get("first_seen", tracker._get_today_str())
    
    # Calculate simple maturity string
    from datetime import date
    days_diff = (date.today() - date.fromisoformat(first_seen)).days
    status = "WARM-UP PHASE" if days_diff <= 14 else "MATURE ACCOUNT"
    
    results["account_meta"] = {
        "handle": handle,
        "first_seen": first_seen,
        "status": status,
        "days_active": days_diff
    }

    results["is_safe_all"] = is_safe_all
    return results

@app.get("/api/health_report")
def get_health_summary():
    from linkedin.usage_tracker import UsageTracker, SAFETY_CONFIG
    from linkedin.conf import ASSETS_DIR, list_active_accounts
    
    tracker = UsageTracker(ASSETS_DIR)
    accounts = list_active_accounts()
    
    report = []
    for handle in accounts:
        # General Usage Stats
        daily_searches = tracker.get_count(handle, "people_searches", "daily")
        daily_cards = tracker.get_count(handle, "harvested_cards", "daily")
        daily_enrich = tracker.get_count(handle, "enrich_profiles", "daily")
        
        # Health Stats
        health = tracker.get_health_stats(handle, timeframe="daily")
        success = health.get("success", 0)
        captchas = health.get("captcha", 0)
        restricted = health.get("restricted", 0)
        timeouts = health.get("timeout", 0)
        failures = health.get("unknown_failure", 0)
        
        total_actions = success + captchas + restricted + timeouts + failures
        success_rate = (success / total_actions * 100) if total_actions > 0 else 100
        
        # Determine Status
        status = "HEALTHY"
        if captchas > 0 or restricted > 0:
            status = "RESTRICTED"
        elif timeouts > 2 or failures > 2:
            status = "UNSTABLE"
            
        report.append({
            "handle": handle,
            "status": status,
            "daily_usage": {
                "searches": daily_searches,
                "cards": daily_cards,
                "enrichment": daily_enrich
            },
            "automation_metrics": {
                "success_rate": round(success_rate, 1),
                "success": success,
                "captchas": captchas,
                "restricted": restricted,
                "timeouts": timeouts,
                "unknown_failure": failures
            },
            "last_failure_note": health.get("last_failure_note", "")
        })
    
    return report
    
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
async def check_replies_now(request: Request):
    """Manually trigger reply checking for a specific account."""
    global current_process
    if current_process and current_process.returncode is None:
        return JSONResponse({"status": "error", "message": "Another process is running."}, status_code=400)
    
    data = await request.json()
    handle = data.get("handle")
    if not handle:
        return JSONResponse({"status": "error", "message": "Handle is required."}, status_code=422)

    cmd = [sys.executable, "-u", "check_replies.py", handle]
    
    current_process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(BASE_DIR)
    )
    
    msg = f"🔍 Checking for new replies on @{handle}..."
    print(f"[SERVER] {msg}")
    await push_log(msg)
    
    asyncio.create_task(read_stream(current_process.stdout))
    asyncio.create_task(read_stream(current_process.stderr))
    
    return {"status": "started", "pid": current_process.pid}



if __name__ == "__main__":
    uvicorn.run("ui_server:app", host="0.0.0.0", port=8000, reload=True)
