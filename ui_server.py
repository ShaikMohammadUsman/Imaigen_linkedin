from typing import List, Any, Optional
import asyncio
import asyncio.subprocess
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
import threading

# Setup
app = FastAPI(title="OpenOutreach API")
BASE_DIR = Path(__file__).parent.absolute()
ASSETS_DIR = BASE_DIR / "assets"
INPUTS_DIR = ASSETS_DIR / "inputs"
# Using the auto-updated detailed CSV
DB_EXPORT_FILE = ASSETS_DIR / "candidates_detailed.csv"

# Global state for process management
current_process = None
db_instances = {}
db_profiles_map = {} # Cache for fast UI loading
last_sync_time = 0
sync_lock = threading.Lock()
csv_cache = {"path": None, "mtime": 0, "records": []}

def get_db(handle: str):
    from linkedin.db.engine import Database
    if handle not in db_instances:
        db_instances[handle] = Database.from_handle(handle)
    return db_instances[handle]

def sync_from_db_background(handle: str, force: bool = False):
    """
    Background task to sync Cloud/Local DB without blocking the UI.
    🛡️ Thread-safe lock + Time-based debounce to prevent DB flooding.
    """
    from linkedin.db.engine import Database
    from linkedin.db.models import Profile
    from linkedin.navigation.enums import ProfileState
    import time
    global db_profiles_map, last_sync_time, sync_lock
    
    # 🛑 1. Debounce check: 5-minute cooldown (300 seconds)
    now = time.time()
    if not force and (now - last_sync_time < 300):
        return

    # 🛑 2. Lock check: Prevent concurrent syncs
    # 🛑 2. Lock check: Prevent concurrent syncs
    if not sync_lock.acquire(blocking=False):
        return

    try:
        # Check Cloud SQL
        if os.getenv("DATABASE_URL"):
            db_wrapper = Database() 
            session = db_wrapper.get_session()
            try:
                q = session.query(Profile).filter(
                    (Profile.state != "discovered") | (Profile.last_message.isnot(None))
                )
                for p in q.all():
                    pid = p.public_identifier
                    db_profiles_map[pid] = {
                        "profile": p.profile,
                        "data": p.data,
                        "state": p.state,
                        "last_message": p.last_message,
                        "last_message_at": p.last_message_at,
                        "transcript": [
                            {
                                "direction": m.direction, 
                                "text": m.text, 
                                "timestamp": m.timestamp.isoformat() if m.timestamp else None,
                                "sender": m.sender_name or (p.profile.get("full_name") if p.profile and isinstance(p.profile, dict) else pid)
                            } for m in p.messages
                        ],
                        "conversation_summary": p.conversation_summary,
                        "conversation_sentiment": p.conversation_sentiment
                    }
                last_sync_time = time.time()
            finally:
                session.close()
                db_wrapper.Session.remove()

        # Also sync Local handle DB
        db_wrapper = get_db(handle)
        session = db_wrapper.get_session()
        try:
            for p in session.query(Profile).all():
                pid = p.public_identifier
                if pid not in db_profiles_map or p.state != "discovered":
                     db_profiles_map[pid] = {
                        "profile": p.profile,
                        "data": p.data,
                        "state": p.state,
                        "last_message": p.last_message,
                        "last_message_at": p.last_message_at,
                        "transcript": [
                            {
                                "direction": m.direction, 
                                "text": m.text, 
                                "timestamp": m.timestamp.isoformat() if m.timestamp else None,
                                "sender": m.sender_name or (p.profile.get("full_name") if p.profile and isinstance(p.profile, dict) else pid)
                            } for m in p.messages
                        ],
                        "conversation_summary": p.conversation_summary,
                        "conversation_sentiment": p.conversation_sentiment
                    }
        finally:
            session.close()
            db_wrapper.Session.remove()
            
    except Exception as e:
        if "connection slots" in str(e):
            print(f"[BACKGROUND SYNC] Cloud SQL is at connection limit. Using memory cache only.")
        else:
            print(f"[BACKGROUND SYNC ERROR] {e}")
    finally:
        sync_lock.release()

from collections import deque

class Broadcaster:
    def __init__(self, history_size=50):
        self.clients = set()
        self.history = deque(maxlen=history_size)

    async def put(self, msg):
        self.history.append(msg)
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
    with open("ui/static/index.html", encoding="utf-8") as f:
        return HTMLResponse(
            content=f.read(),
            headers={"Cache-Control": "no-store, max-age=0"},
        )

@app.get("/api/logs")
async def sse_logs(request: Request):
    """Server-Sent Events context for logging."""
    print(f"🔌 [SERVER] New log stream connection established from {request.client.host}")
    
    async def event_generator():
        queue = asyncio.Queue()
        
        # Push history first so user sees what just happened
        for msg in broadcaster.history:
            await queue.put(msg)
            
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
    source = data.get("source", "LinkedIn")

    if not handle or not search_url:
        return JSONResponse({"status": "error", "message": "Handle and Search URL are required."}, status_code=422)

    cmd = [
        sys.executable, "-u", "harvest_search.py", 
        handle, search_url, str(start_page), str(pages), job_id, role_name, company_name, app_link, location, compensation, source
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
async def download_csv():
    """Generates a dynamic CSV containing all intelligence (including transcripts)."""
    try:
        data_response = get_results()
        records = data_response.get("data", [])
        
        if not records:
            return JSONResponse({"status": "error", "message": "No data available to export"}, status_code=404)
        
        # Define fields and clean records for CSV
        import csv
        import io
        from fastapi.responses import StreamingResponse
        
        # Flatten and prepare for CSV
        output = io.StringIO()
        fieldnames = [
            "Full Name", "Role", "Headline", "Current Company", "Location", 
            "Email", "Phone", "Status", "URL", "Source", "Last Message", 
            "Last Received Message", "Transcript"
        ]
        writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction='ignore')
        writer.writeheader()
        
        for r in records:
            # Flatten transcript list into a single string
            transcript_list = r.get("Transcript", [])
            transcript_str = ""
            for m in transcript_list:
                ts = m.get("timestamp", "").replace("T", " ")[:16]
                transcript_str += f"[{ts}] {m.get('sender')}: {m.get('text')}\n"
            
            row = r.copy()
            row["Transcript"] = transcript_str.strip()
            writer.writerow(row)
            
        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=scooter_outreach_report.csv"}
        )
    except Exception as e:
        print(f"Failed to generate dynamic CSV: {e}")
        # Fallback to static file if dynamic fails
        file_path = ASSETS_DIR / "inputs" / "harvested_urls.csv"
        if file_path.exists():
            return FileResponse(file_path, media_type='text/csv', filename="harvested_candidates.csv")
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

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
    except Exception as e:
        print(f"Error in cleanup: {e}")
        
    if count > 0:
        msg = f"🧹 [SERVER] Cleaned up {count} orphaned bot processes."
        print(msg)
        await push_log(msg)
    
    return {"status": "stopped"}

@app.get("/api/results")
def get_results(background_tasks: BackgroundTasks, handle: str = None, refresh: bool = False):
    """
    Returns the final global results list, enriched with profile data.
    If refresh=true, it re-scans all DBs.
    """
    # Defensive: when the UI hasn't selected an account yet, `handle` can be empty.
    # This endpoint is expensive (DB syncing / enrichment map build), so return quickly
    # to avoid blocking the UI (dropdown meters, accounts list, etc.).
    if not handle or handle == "undefined":
        return {"data": [], "stats": {"total": 0}}

    try:
        from linkedin.db.profiles import url_to_public_id
        from fastapi.responses import JSONResponse
        
        global db_profiles_map
        
        # ⚡ OPTIMIZATION: Background Sync
        # Launch heavy Cloud SQL/Local DB sync in the background so UI doesn't hang.
        background_tasks.add_task(sync_from_db_background, handle)

        # 1. Load Master Queue (CSV with simple cache)
        global csv_cache
        queue_records = []
        if HARVEST_FILE.exists():
            try:
                mtime = os.path.getmtime(HARVEST_FILE)
                if csv_cache["path"] == str(HARVEST_FILE) and csv_cache["mtime"] == mtime:
                    queue_records = csv_cache["records"]
                else:
                    import pandas as pd
                    import csv
                    with open(HARVEST_FILE, "r", encoding="utf-8") as f:
                        reader = csv.reader(f)
                        header = next(reader, [])
                        if len(header) > 0 and 'source' not in header:
                            header.append('source')
                        
                        rows = list(reader)
                        data = []
                        for row in rows:
                            while len(row) < len(header):
                                row.append("")
                            data.append(row[:len(header)])
                    
                    df = pd.DataFrame(data, columns=header)
                    df.fillna("", inplace=True)
                    df.drop_duplicates(subset=['url'], keep='last', inplace=True)
                    queue_records = df.to_dict('records')
                    queue_records.reverse()
                    
                    # Update cache
                    csv_cache = {"path": str(HARVEST_FILE), "mtime": mtime, "records": queue_records}
            except Exception as e:
                print(f"Error reading harvest file: {e}")

        # 2. Build Final Dataset using Global Map
        final_results = []
        processed_pids = set()

        for row in queue_records:
            # Case-insensitive URL lookup
            url = row.get("url") or row.get("URL") or row.get("LinkedIn URL") or ""
            if not url: continue
            
            pid = None
            try: pid = url_to_public_id(url)
            except: pass
            
            record = {
                "Full Name": row.get("candidate_name") or (pid.capitalize() if pid else "Harvested Profile"),
                "Role": row.get("role_name") or "Generic",
                "Headline": "-",
                "Current Company": row.get("company_name") or "-",
                "Location": row.get("location") or "-",
                "Status": "HARVESTED",
                "URL": url,
                "LinkedIn URL": url,
                "Source": row.get("source") or "LinkedIn",
                "Picture": row.get("candidate_pic") or "",
                "Transcript": []
            }

            if pid and pid in db_profiles_map:
                snap = db_profiles_map[pid]
                prof = snap.get("profile") or {}
                
                if isinstance(prof, str):
                    try: 
                        import json
                        prof = json.loads(prof)
                    except: prof = {}
                
                if isinstance(prof, dict):
                    exp = prof.get("positions", [])
                    company = record["Current Company"]
                    if exp and isinstance(exp, list) and len(exp) > 0:
                        company = exp[0].get("company_name") or company
                    
                    # Advanced Mapping for Details Modal
                    exp_list = []
                    for pos in prof.get("positions", []):
                        dr = pos.get("date_range") or {}
                        start = dr.get("start") or {}
                        end = dr.get("end") or {}
                        
                        start_str = f"{start.get('month') or '?'}/{start.get('year') or '?'}" if start else "?"
                        end_val = f"{end.get('month') or '?'}/{end.get('year')}" if (end and end.get('year')) else "Present"
                        dates = f"{start_str} - {end_val}"
                        
                        comp_info = pos.get("company_details") or {}
                        
                        exp_list.append({
                            "title": pos.get("title") or "Position",
                            "company": pos.get("company_name") or "Company",
                            "dates": dates,
                            "description": pos.get("description"),
                            "company_description": comp_info.get("description"),
                            "company_industry": comp_info.get("industry"),
                            "company_size": comp_info.get("employee_count"),
                            "company_website": comp_info.get("url")
                        })
                    
                    edu_list = []
                    for edu in prof.get("educations", []):
                        dr = edu.get("date_range") or {}
                        s_year = (dr.get("start") or {}).get("year") or "?"
                        e_year = (dr.get("end") or {}).get("year") or "?"
                        edu_list.append({
                            "school_name": edu.get("school_name") or "School",
                            "degree_name": edu.get("degree_name") or "Degree",
                            "field_of_study": edu.get("field_of_study"),
                            "dates": f"{s_year} - {e_year}"
                        })

                    record.update({
                        "Full Name": prof.get("full_name") or record["Full Name"],
                        "Headline": prof.get("headline") or "-",
                        "Current Company": company,
                        "Location": prof.get("location_name") or record["Location"],
                        "Status": snap.get("state", "UNKNOWN").upper(),
                        "Transcript": snap.get("transcript", []),
                        "Picture": prof.get("profile_picture") or record["Picture"],
                        "Conversation Summary": snap.get("conversation_summary"),
                        "Conversation Sentiment": snap.get("conversation_sentiment"),
                        "About": prof.get("summary"),
                        "Experience": exp_list,
                        "Education": edu_list,
                        "Skills": prof.get("skills", []),
                        "Certifications": prof.get("certifications", []),
                        "projects": prof.get("projects", [])
                    })
                    record["public_id"] = pid
                processed_pids.add(pid)
            
            final_results.append(record)

        # 3. Add Orphans (Enriched profiles not in CSV)
        for pid, snap in db_profiles_map.items():
            if pid not in processed_pids and snap.get("state") != "discovered":
                prof = snap.get("profile") or {}
                if isinstance(prof, str):
                    try:
                        import json
                        prof = json.loads(prof)
                    except: prof = {}
                
                if isinstance(prof, dict):
                    final_results.append({
                        "Full Name": prof.get("full_name") or pid,
                        "Role": "Direct Enrichment",
                        "Headline": prof.get("headline") or "-",
                        "Current Company": "-",
                        "Location": prof.get("location_name") or "-",
                        "Status": snap.get("state", "UNKNOWN").upper(),
                        "URL": f"https://www.linkedin.com/in/{pid}",
                        "LinkedIn URL": f"https://www.linkedin.com/in/{pid}",
                        "Source": "Database",
                        "Picture": prof.get("profile_picture") or "",
                        "Transcript": snap.get("transcript", []),
                        "Conversation Summary": snap.get("conversation_summary"),
                        "Conversation Sentiment": snap.get("conversation_sentiment"),
                        "public_id": pid
                    })

        return {
            "status": "ok",
            "data": final_results,
            "stats": {
                "total": len(final_results),
                "enriched": len(processed_pids)
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
    categories = ["enrich_profiles", "harvested_cards", "people_searches", "connection_requests"]
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
        
        # Sessions and Rates
        sessions = tracker.get_count(handle, "sessions_started", "daily")
        total_actions = success + captchas + restricted + timeouts + failures
        success_rate = (success / total_actions * 100) if total_actions > 0 else 100
        challenge_rate = ((captchas + restricted) / total_actions * 100) if total_actions > 0 else 0
        
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
                "challenge_rate": round(challenge_rate, 1),
                "session_count": sessions,
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
def get_queue(background_tasks: BackgroundTasks, handle: str = None):
    if not handle or handle == "undefined":
        return []

    # ⚡ Trigger background sync
    background_tasks.add_task(sync_from_db_background, handle)

    if not HARVEST_FILE.exists():
        return []
    
    # 1. Load Master Queue (CSV with simple cache)
    global csv_cache
    queue_records = []
    if HARVEST_FILE.exists():
        try:
            mtime = os.path.getmtime(HARVEST_FILE)
            if csv_cache["path"] == str(HARVEST_FILE) and csv_cache["mtime"] == mtime:
                queue_records = csv_cache["records"]
            else:
                import csv
                import pandas as pd
                with open(HARVEST_FILE, "r", encoding="utf-8") as f:
                    reader = csv.reader(f)
                    header = next(reader, [])
                    if len(header) > 0 and 'source' not in header:
                        header.append('source')
                    
                    rows = list(reader)
                    data = []
                    for row in rows:
                        while len(row) < len(header):
                            row.append("")
                        data.append(row[:len(header)])
                
                df = pd.DataFrame(data, columns=header)
                df.fillna("", inplace=True)
                df.drop_duplicates(subset=['url'], keep='last', inplace=True)
                queue_records = df.to_dict('records')
                queue_records.reverse()
                
                # Update cache
                csv_cache = {"path": str(HARVEST_FILE), "mtime": mtime, "records": queue_records}
        except Exception as e:
            print(f"Error reading harvest file in get_queue: {e}")
            return []

    # Enrich with DB Status using Global Cache
    final_queue = []
    for row in queue_records:
        url = row.get("url", "")
        if not url: continue
        
        pid = None
        try: pid = url_to_public_id(url)
        except: pass
        
        display_name = row.get("candidate_name") or (pid.capitalize() if pid else "Harvested Profile")
        
        # Base item
        item = {
            "full_name": display_name,
            "linkedin_url": url,
            "company": row.get("company_name", "-"),
            "location": row.get("location", "-"),
            "role_name": row.get("role_name", "Generic"),
            "status": "harvested",
            "headline": "Pending enrichment...",
            "picture": row.get("candidate_pic", "")
        }

        # Enrich from Cache
        if pid and pid in db_profiles_map:
            snap = db_profiles_map[pid]
            item["status"] = snap.get("state", "harvested").lower()
            
            prof = snap.get("profile") or {}
            if isinstance(prof, str):
                try: 
                    import json
                    prof = json.loads(prof)
                except: prof = {}
            
            if isinstance(prof, dict):
                # Advanced Mapping for Details Modal
                exp_list = []
                for pos in prof.get("positions", []):
                    dr = pos.get("date_range") or {}
                    start = dr.get("start") or {}
                    end = dr.get("end") or {}
                    
                    start_str = f"{start.get('month') or '?'}/{start.get('year') or '?'}" if start else "?"
                    end_val = f"{end.get('month') or '?'}/{end.get('year')}" if (end and end.get('year')) else "Present"
                    dates = f"{start_str} - {end_val}"
                    
                    comp_info = pos.get("company_details") or {}
                    
                    exp_list.append({
                        "title": pos.get("title") or "Position",
                        "company": pos.get("company_name") or "Company",
                        "dates": dates,
                        "description": pos.get("description"),
                        "company_description": comp_info.get("description"),
                        "company_industry": comp_info.get("industry"),
                        "company_size": comp_info.get("employee_count"),
                        "company_website": comp_info.get("url")
                    })
                
                edu_list = []
                for edu in prof.get("educations", []):
                    dr = edu.get("date_range") or {}
                    s_year = (dr.get("start") or {}).get("year") or "?"
                    e_year = (dr.get("end") or {}).get("year") or "?"
                    edu_list.append({
                        "school_name": edu.get("school_name") or "School",
                        "degree_name": edu.get("degree_name") or "Degree",
                        "field_of_study": edu.get("field_of_study"),
                        "dates": f"{s_year} - {e_year}"
                    })

                item.update({
                    "full_name": prof.get("full_name") or item["full_name"],
                    "headline": prof.get("headline") or item["headline"],
                    "picture": prof.get("profile_picture") or item["picture"],
                    "Status": snap.get("state", "harvested").upper(),
                    "Transcript": snap.get("transcript", []),
                    "Conversation Summary": snap.get("conversation_summary"),
                    "Conversation Sentiment": snap.get("conversation_sentiment"),
                    "About": prof.get("summary"),
                    "Experience": exp_list,
                    "Education": edu_list,
                    "Skills": prof.get("skills", []),
                    "Certifications": prof.get("certifications", []),
                    "Projects": prof.get("projects", [])
                })
        
        final_queue.append(item)

    return final_queue

@app.post("/api/queue")
async def save_queue(request: Request):
    """Overwrite the queue with new list (after user edits/deletions)"""
    new_data = await request.json()
    import csv
    
    # Ensure directory exists (it should, but safety first)
    HARVEST_FILE.parent.mkdir(parents=True, exist_ok=True)
    
    fieldnames = ["url", "job_id", "role_name", "company_name", "app_link", "location", "compensation", "candidate_name", "candidate_pic", "source"]
    
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



@app.post("/api/preview_draft")
async def preview_draft(request: Request):
    """Generate a preview of the AI connection note for a candidate"""
    data = await request.json()
    handle = data.get("handle")
    url = data.get("url")
    if not handle or not url:
        return JSONResponse({"status": "error", "message": "Handle and URL are required."}, status_code=422)

    from linkedin.db.engine import Database
    from linkedin.db.profiles import url_to_public_id
    from linkedin.db.models import Profile
    from linkedin.sessions.registry import get_session
    from linkedin.templates.renderer import render_template
    import csv
    
    session = get_session(handle=handle)
    
    if not HARVEST_FILE.exists():
        return JSONResponse({"status": "error", "message": "Queue is empty."}, status_code=400)
        
    target_row = None
    with open(HARVEST_FILE, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("url") == url:
                target_row = row
                break
                
    if not target_row:
        return JSONResponse({"status": "error", "message": "Candidate not found in queue."}, status_code=404)
        
    profile = target_row.copy()
    # Normalize keys if needed
    if "candidate_name" in profile:
        profile["first_name"] = profile["candidate_name"].split()[0]
        
    try:
        from linkedin.db.engine import Database
        db_wrapper = get_db(handle)
        db_session = db_wrapper.get_session()
        try:
            pid = url_to_public_id(url)
            db_prof = db_session.query(Profile).filter(Profile.public_identifier == pid).first()
            if db_prof and db_prof.profile:
                p_data = db_prof.profile
                if isinstance(p_data, str):
                    import json
                    try: p_data = json.loads(p_data)
                    except: p_data = {}
                for k, v in p_data.items():
                    if k not in profile: profile[k] = v
                if "experience" in p_data and isinstance(p_data["experience"], list) and len(p_data["experience"]) > 0:
                    profile["positions"] = [{"company_name": p_data["experience"][0].get("company", "their current company")}]
                if "name" in p_data:
                    profile["first_name"] = p_data["name"].split()[0]
        finally:
            db_session.close()
            db_wrapper.Session.remove()
    except Exception as e:
        print(f"Draft Preview DB error: {e}")
         
    template_file = session.config.get("connection_template", "templates/prompts/invite.j2")
    template_type = session.config.get("connection_template_type", "ai_prompt")
    
    try:
        # Resolve path
        from linkedin.conf import ASSETS_DIR
        tf = ASSETS_DIR / template_file
        if not tf.exists():
            return JSONResponse({"status": "error", "message": f"Template not found: {template_file}"}, status_code=404)
            
        note = render_template(session, str(tf), template_type, profile, include_link=False)
        return {"status": "ok", "note": note}
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


# ─── SCREENING API ───────────────────────────────────────────────────
# Role Profile Management + Candidate Screening (Gate 1 + Gate 2)
# ─────────────────────────────────────────────────────────────────────

@app.get("/api/role_profiles")
def api_list_role_profiles():
    """List all saved role profiles."""
    from linkedin.screening.engine import list_role_profiles
    return list_role_profiles()


@app.post("/api/role_profiles")
async def api_save_role_profile(request: Request):
    """
    Create or update a role profile (supports staged creation).
    Body should contain stage1, stage2, stage3 fields + optional role_id.
    """
    from linkedin.screening.engine import save_role_profile, load_role_profile

    data = await request.json()
    role_id = data.get("role_id")

    # If updating an existing profile, merge with existing data
    if role_id:
        existing = load_role_profile(role_id)
        if existing:
            # Merge: new data overrides existing
            for key in ("stage1", "stage2", "stage3"):
                if key in data and data[key]:
                    existing[key] = data[key]
            if "isCompleted" in data:
                existing["isCompleted"] = data["isCompleted"]
            if "status" in data:
                existing["status"] = data["status"]
            data = existing

    # Auto-complete status if all 3 stages present
    if data.get("isCompleted") and data.get("stage1") and data.get("stage2") and data.get("stage3"):
        data["status"] = "active"

    saved_id = save_role_profile(data)
    return {"status": "ok", "role_id": saved_id, "message": "Role profile saved"}


@app.get("/api/role_profiles/{role_id}")
def api_get_role_profile(role_id: str):
    """Get a specific role profile by ID."""
    from linkedin.screening.engine import load_role_profile
    profile = load_role_profile(role_id)
    if not profile:
        return JSONResponse({"status": "error", "message": "Role profile not found"}, status_code=404)
    return profile


@app.delete("/api/role_profiles/{role_id}")
def api_delete_role_profile(role_id: str):
    """Delete a role profile."""
    from linkedin.screening.engine import delete_role_profile
    success = delete_role_profile(role_id)
    if not success:
        return JSONResponse({"status": "error", "message": "Role profile not found"}, status_code=404)
    return {"status": "ok", "message": "Role profile deleted"}


@app.post("/api/screen_candidate")
async def api_screen_candidate(request: Request):
    """
    Screen a single enriched candidate against a role profile.

    Body:
    {
        "handle": "account_handle",
        "public_id": "linkedin-public-id",
        "role_id": "role-profile-id"
    }
    """
    from linkedin.screening.engine import (
        screen_candidate, load_role_profile, synthesize_resume_text
    )

    data = await request.json()
    handle = data.get("handle")
    public_id = data.get("public_id")
    role_id = data.get("role_id")

    if not all([handle, public_id, role_id]):
        return JSONResponse(
            {"status": "error", "message": "handle, public_id, and role_id are required"},
            status_code=422
        )

    # Load role profile
    role_profile = load_role_profile(role_id)
    if not role_profile:
        return JSONResponse({"status": "error", "message": f"Role profile '{role_id}' not found"}, status_code=404)

    if not role_profile.get("isCompleted"):
        return JSONResponse(
            {"status": "error", "message": "Role profile must be completed (all 3 stages) before screening"},
            status_code=400
        )

    # Load candidate profile from DB
    profile_data = None

    # First try global cache
    if public_id in db_profiles_map:
        snap = db_profiles_map[public_id]
        profile_data = snap.get("profile", {})
        if isinstance(profile_data, str):
            import json as _json
            try:
                profile_data = _json.loads(profile_data)
            except:
                profile_data = {}

    # If not in cache, try DB directly
    if not profile_data:
        try:
            db_wrapper = get_db(handle)
            session = db_wrapper.get_session()
            try:
                profile_row = session.query(Profile).filter(
                    Profile.public_identifier == public_id
                ).first()
                if profile_row and profile_row.profile:
                    profile_data = profile_row.profile
                    if isinstance(profile_data, str):
                        import json as _json
                        try:
                            profile_data = _json.loads(profile_data)
                        except:
                            profile_data = {}
            finally:
                session.close()
                db_wrapper.Session.remove()
        except Exception as e:
            return JSONResponse(
                {"status": "error", "message": f"Failed to load profile: {e}"},
                status_code=500
            )

    if not profile_data or not isinstance(profile_data, dict):
        return JSONResponse(
            {"status": "error", "message": f"No enriched profile data found for '{public_id}'. Enrich the candidate first."},
            status_code=404
        )

    # Add public_identifier to profile_data for result tracking
    profile_data["public_identifier"] = public_id

    # Run screening
    try:
        result = screen_candidate(profile_data, role_profile, handle)
        return {"status": "ok", "result": result}
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JSONResponse(
            {"status": "error", "message": f"Screening failed: {e}"},
            status_code=500
        )


@app.post("/api/screen_batch")
async def api_screen_batch(request: Request):
    """
    Screen all enriched candidates for a handle against a role profile.

    Body:
    {
        "handle": "account_handle",
        "role_id": "role-profile-id",
        "public_ids": ["id1", "id2"]  // optional — if omitted, screens ALL enriched profiles
    }
    """
    from linkedin.screening.engine import (
        screen_batch, load_role_profile
    )

    data = await request.json()
    handle = data.get("handle")
    role_id = data.get("role_id")
    selected_ids = data.get("public_ids")  # optional filter
    selected_urls = data.get("urls")        # optional filter

    if not handle or not role_id:
        return JSONResponse(
            {"status": "error", "message": "handle and role_id are required"},
            status_code=422
        )

    # Load role profile
    role_profile = load_role_profile(role_id)
    if not role_profile:
        return JSONResponse({"status": "error", "message": f"Role profile '{role_id}' not found"}, status_code=404)

    if not role_profile.get("isCompleted"):
        return JSONResponse(
            {"status": "error", "message": "Role profile must be completed (all 3 stages) before screening"},
            status_code=400
        )

    # Load all enriched profiles from DB
    profiles_to_screen = []
    try:
        db_wrapper = get_db(handle)
        session = db_wrapper.get_session()
        try:
            query = session.query(Profile).filter(Profile.state != "discovered")

            if not selected_ids and selected_urls:
                from linkedin.db.profiles import url_to_public_id
                selected_ids = [url_to_public_id(u) for u in selected_urls]

            if selected_ids:
                query = query.filter(Profile.public_identifier.in_(selected_ids))

            for p in query.all():
                profile_data = p.profile
                if not profile_data:
                    continue
                if isinstance(profile_data, str):
                    import json as _json
                    try:
                        profile_data = _json.loads(profile_data)
                    except:
                        continue
                if isinstance(profile_data, dict):
                    profile_data["public_identifier"] = p.public_identifier
                    profiles_to_screen.append(profile_data)
        finally:
            session.close()
            db_wrapper.Session.remove()
    except Exception as e:
        return JSONResponse(
            {"status": "error", "message": f"Failed to load profiles: {e}"},
            status_code=500
        )

    if not profiles_to_screen:
        return JSONResponse(
            {"status": "error", "message": "No enriched profiles found to screen"},
            status_code=404
        )

    # Run batch screening
    try:
        results = screen_batch(profiles_to_screen, role_profile, handle)

        # Summary stats
        tiers = {}
        for r in results:
            t = r.get("tier") or "NO_SCORE"
            tiers[t] = tiers.get(t, 0) + 1

        return {
            "status": "ok",
            "total_screened": len(results),
            "tier_summary": tiers,
            "results": results
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JSONResponse(
            {"status": "error", "message": f"Batch screening failed: {e}"},
            status_code=500
        )


@app.get("/api/screening_results")
def api_get_screening_results(handle: str, role_id: Optional[str] = None):
    """Get screening results for a handle, optionally filtered by role_id."""
    from linkedin.screening.engine import list_screening_results

    if not handle or handle == "undefined":
        return {"results": [], "total": 0}

    results = list_screening_results(handle, role_id)

    # Summary stats
    tiers = {}
    for r in results:
        t = r.get("tier") or "NO_SCORE"
        tiers[t] = tiers.get(t, 0) + 1

    return {
        "results": results,
        "total": len(results),
        "tier_summary": tiers
    }


if __name__ == "__main__":
    # `reload=True` can peg CPU on large repos (file watching), which can cause the
    # UI to get stuck (meters remain 0/0) because API responses don't come back.
    # Enable explicitly by setting UVICORN_RELOAD=1.
    reload_flag = os.getenv("UVICORN_RELOAD", "").strip().lower() in {"1", "true", "yes", "on"}
    uvicorn.run("ui_server:app", host="0.0.0.0", port=8000, reload=reload_flag)
