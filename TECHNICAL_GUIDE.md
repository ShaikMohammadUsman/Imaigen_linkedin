# 🛠️ LinkedIn Automation - Technical Documentation

## 🏗️ System Architecture

The application is built using a **Python + Playwright** backend with a **FastAPI** layer serving a static HTML/JS frontend.

### Core Components:

1. **`ui_server.py` (FastAPI Backend)**
   - Acts as the central command center.
   - Exposes endpoints (`/api/harvest`, `/api/campaign`, `/api/stop`).
   - Manages background subprocesses for scraping tasks.
   - Streams real-time logs via SSE (Server-Sent Events).

2. **`harvest_search.py` (Sourcing Engine)**
   - **Input**: Search URL, Page Count.
   - **Logic**: 
     - Iterates through search pagination.
     - Uses **15+ Fallback Selectors** to handle dynamic CSS classes.
     - Implements **Dynamic Smart Wait** (scroll + retry) for timeouts.
     - Saves results to `assets/inputs/harvested_urls.csv`.

3. **`main.py` (Enrichment & Outreach Engine)**
   - **Input**: List of Profile URLs.
   - **Logic**:
     - checks `Profile` state in SQLite DB (`discovered` -> `enriched` -> `connected`).
     - Extracts extensive data (Experience, Education, Skills).
     - Sends connection invites using `linkedin/actions/connect.py`.
   
4. **`check_replies.py` (Reply Tracker)**
   - **Daemon**: Can run as a background service or triggered manually.
   - **Logic**: Scans "COMPLETED" profiles for new incoming messages.
   - Updates `last_received_message` in DB.

---

## ⚙️ Configuration & Limits

Limits are defined in **`linkedin/usage_tracker.py`**:

```python
# Default Hard Limits
HARVEST_PAGE_LIMIT = 50  # Max search pages to scan per day
ENRICH_PROFILE_LIMIT = 20 # Max profile visits per day (Safe Mode)
```

**Note**: The system checks `assets/usage_stats.json` before every action. If the limit is reached, it throws a `🛑 Limit Reached` error and aborts to protect the account.

---

## 🔒 Anti-Detection Mechanisms

### 1. **Human-Like Delays** (`linkedin/sessions/account.py`)
Instead of static `time.sleep(5)`, we use a custom `human_delay()` function:

```python
def human_delay(min_val, max_val):
    # Adds random variance to bounds
    # Generates values like: 3.27s, 5.81s, 7.42s
    # Never repeats the exact same duration
    ...
```

### 2. **Browser Fingerprinting**
- Uses **Playwright Stealth** (implied) to mask webdriver properties.
- Reuses existing **user cookies** (`assets/cookies/{handle}.json`) to avoid re-login triggers.
- Mimics **mouse scrolling** behavior before clicking elements.

---

## 🗄️ Database Schema

We use **SQLite** (via SQLAlchemy) for local state management.
**File**: `assets/data/{email}.db`

**Table: `profiles`**
| Column | Type | Description |
| :--- | :--- | :--- |
| `public_identifier` | STRING | Primary Key (e.g., `john-doe-123`) |
| `state` | STRING | `discovered`, `enriched`, `connected`, `completed` |
| `profile_data` | JSON | Full raw profile dump |
| `last_message` | STRING | Content of last sent message |
| `last_received_message` | STRING | Content of candidate's reply |

---

## 🐛 Troubleshooting Guide

### 1. **Harvest Returns 0 Results**
- **Cause**: LinkedIn changed CSS class names.
- **Fix**: Check `debug_harvest_page_*.png` screenshots. Update selectors in `harvest_search.py`.

### 2. **State Save Failed Error**
- **Cause**: `harvest_state.json` corrupted.
- **Fix**: Run `echo "{}" > assets/inputs/harvest_state.json` to reset.

### 3. **Process Won't Stop**
- **Fix**: The UI "Stop Bot" button sends `SIGTERM` to the storage `current_process`.

---

## 🚀 Environment Setup

```bash
# Install Dependencies
pip install -r requirements.txt
playwright install chromium

# Run Server
./venv/bin/uvicorn ui_server:app --host 0.0.0.0 --port 8000 --reload
```
