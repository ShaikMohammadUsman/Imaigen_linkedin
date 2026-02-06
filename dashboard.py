import streamlit as st
import pandas as pd
import subprocess
import os
import signal
import psutil
from pathlib import Path
import time
import sys

# Page Config
st.set_page_config(page_title="OpenOutreach UI", layout="wide", page_icon="🕵️")

# Paths
BASE_DIR = Path(__file__).parent.absolute()
ASSETS_DIR = BASE_DIR / "assets"
INPUTS_DIR = ASSETS_DIR / "inputs"
SECRETS_FILE = ASSETS_DIR / "accounts.secrets.yaml"
HARVEST_FILE = INPUTS_DIR / "harvested_urls.csv"
DB_EXPORT_FILE = ASSETS_DIR / "candidates_export.csv"

# Helper to run commands
def run_command(command, log_placeholder=None):
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        shell=True,
        cwd=str(BASE_DIR),
        text=True,
        bufsize=1,
        universal_newlines=True
    )
    
    if log_placeholder:
        lines = []
        for line in process.stdout:
            lines.append(line)
            # update log view (keep last 20 lines)
            log_placeholder.code("".join(lines[-20:]))
    
    process.wait()
    return process.returncode

# ---------------------------------------------------------
# Sidebar: Setup & Accounts
# ---------------------------------------------------------
st.sidebar.title("🤖 OpenOutreach")
st.sidebar.markdown("---")

# Account Selector
# (In a real app, parse YAML, but for now we assume 1 account or passed via arg)
handle = st.sidebar.text_input("LinkedIn Handle (Email)", value="vishnu02.tech@gmail.com")

st.sidebar.markdown("### 🚦 Status")
# Check if main.py is running
def is_bot_running():
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            if 'python' in proc.info['name'] and 'main.py' in " ".join(proc.info['cmdline']):
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    return False

if is_bot_running():
    st.sidebar.success("Bot is RUNNING 🏃")
    if st.sidebar.button("Stop Bot"):
        # Kill command (Unix)
        os.system("pkill -f main.py")
        st.experimental_rerun()
else:
    st.sidebar.warning("Bot is STOPPED 🛑")


# ---------------------------------------------------------
# Tab 1: Harvest (Search)
# ---------------------------------------------------------
tab1, tab2, tab3, tab4 = st.tabs(["🌾 Harvest Leads", "🚀 Campaign", "📊 Results", "⚙️ Config"])

with tab1:
    st.header("Step 1: Harvest Candidates")
    st.markdown("""
    1. Go to LinkedIn Search, filter your candidates.
    2. Copy the URL from your browser.
    3. Paste it below to grab the profile links.
    """)
    
    col1, col2 = st.columns([3, 1])
    search_url = col1.text_input("LinkedIn Search URL")
    job_id = col2.text_input("Job ID (for tagging)", value="694bc34bb598b0da1f52054c")
    
    c1, c2, c3 = st.columns(3)
    start_page = c1.number_input("Start Page", min_value=1, value=1)
    num_pages = c2.number_input("Pages to Scrape", min_value=1, max_value=10, value=5)
    
    if st.button("Start Harvesting"):
        if not search_url:
            st.error("Please enter a Search URL")
        else:
            cmd = f"./venv/bin/python harvest_search.py {handle} '{search_url}' {start_page} {num_pages} {job_id}"
            
            st.info(f"Running: {cmd}")
            log_box = st.empty()
            run_command(cmd, log_box)
            st.success("Harvest Complete! ✅")
            
    # Show Preview of Harvested CSV
    if HARVEST_FILE.exists():
        st.subheader("Current Harvest List")
        df = pd.read_csv(HARVEST_FILE)
        st.dataframe(df.tail(10))
        st.caption(f"Total Profiles: {len(df)}")

# ---------------------------------------------------------
# Tab 2: Campaign (Main Bot)
# ---------------------------------------------------------
with tab2:
    st.header("Step 2: Run Campaign")
    st.markdown("This will visit the profiles from the Harvest List, connect, and send messages.")
    
    c1, c2 = st.columns(2)
    c1.metric("Max Daily Actions", "20")
    c2.metric("Delay (min)", "45s")
    
    if st.button("🚀 Launch Main Bot"):
        # We start it as a subprocess so UI doesn't freeze entirely, 
        # but we want to stream logs.
        cmd = f"./venv/bin/python main.py {handle}"
        st.text("Launching bot behavior... check the logs below.")
        
        # Create a container specifically for logs
        log_container = st.empty()
        
        # Run safely
        run_command(cmd, log_container)
        st.success("Daily run finished! 🎉")

# ---------------------------------------------------------
# Tab 3: Analyze Results
# ---------------------------------------------------------
with tab3:
    st.header("Step 3: Analyze & Export")
    
    if st.button("🔄 Refresh Data from Database"):
        cmd = f"./venv/bin/python export_candidates.py {handle}"
        run_command(cmd)
        st.success("Data exported from DB!")
        
    if DB_EXPORT_FILE.exists():
        df_results = pd.read_csv(DB_EXPORT_FILE)
        
        # Stats
        st.markdown("### Campaign Stats")
        col1, col2, col3 = st.columns(3)
        col1.metric("Total Scraped", len(df_results))
        
        # Count connections
        if "Status" in df_results.columns:
            connected = df_results[df_results["Status"] == "connected"].shape[0]
            pending = df_results[df_results["Status"] == "pending"].shape[0]
            col2.metric("Connected", connected)
            col3.metric("Pending", pending)
        
        st.markdown("### Candidate Details")
        st.dataframe(df_results)
        
        with open(DB_EXPORT_FILE, "rb") as f:
            st.download_button("Download CSV", f, file_name="linkedin_candidates.csv")
    else:
        st.info("No results exported yet. Run the 'Refresh' button.")

# ---------------------------------------------------------
# Tab 4: Config
# ---------------------------------------------------------
with tab4:
    st.header("Configuration")
    
    st.subheader(".env File")
    if Path(".env").exists():
        with open(".env", "r") as f:
            env_content = f.read()
        st.code(env_content, language="bash")
    else:
        st.warning(".env file missing!")
        
    st.subheader("Secrets File")
    if SECRETS_FILE.exists():
        with open(SECRETS_FILE, "r") as f:
            yaml_content = f.read()
        st.code(yaml_content, language="yaml")
