# Scooter AI: LinkedIn Safety & Human-Mimicry Strategy

This document outlines the **Ultra-Ultra Conservative** automation strategy implemented for LinkedIn operations. Our primary goal is account longevity and stealth by operating far below LinkedIn's detection thresholds.

---

## 1. Core Philosophy
Instead of "how much can we scrape," we ask "how little can we do to remain valuable." We mimic a **Light Human Recruiter** who takes breaks, works standard business hours, and doesn't have perfectly consistent daily volumes.

---

## 2. Quota Management (The Numbers)

We use a four-tier limit system: **Session-Randomized**, **Daily Patterned**, **Weekly Targeted**, and **Monthly Capped**. These are all tracked in real-time on your dashboard sidebar.

### A. People Searches & Pagination (Discovery)
Limits how many times you hit the search results page.
- **Monthly Target**: 80 – 120 searches total.
- **Hard Daily Search Cap**: 6 searches maximum.
- **Pages Per Session**: **1 – 6 pages max**. The system auto-picks a dynamic number (e.g., 2, 3, or 4) for each specific session to keep activity irregular.
- **Automatic Continuation**: The system remembers exactly where you left off for every search URL. If you run the same search twice, it will automatically start from the next unvisited page (e.g., Page 4) instead of starting from Page 1.
- **Weekly Schedule**:
    *   **Mon/Fri (Normal)**: 2 – 4 searches.
    - **Tue/Wed/Thu (Heavy)**: 5 – 6 searches.
    - **Saturday (Light)**: 0 – 2 searches.
    - **Sunday (Off)**: 0 – 1 search.

### B. Lead Harvesting (Cards)
Limits how many names/mini-profiles are stored from searches.
- **Daily Target**: **100 – 180 cards** (at ~10 cards per page, this is roughly 10-18 total pages viewed per day).
- **Hard Cap**: 300 – 500 cards (System will block execution if reached).

### C. Profile enrichment (Full Views)
- **Daily Normal**: **0 – 10 profile opens**.
- **Hard Cap**: 13 profile opens per day.

---

## 3. Session Distribution (The "Human Spread")

We don't do all work in one go. The system is designed to spread activity across the day to look like a human recruiter checking in between other tasks.

### The Two-Session Pattern
We recommend splitting your daily quota into two distinct windows:
1.  **Morning Session (09:30 – 12:30 IST)**:
    - Perform 1-2 searches.
    - Harvest 40-80 cards.
    - Enrich 3-5 profiles.
2.  **Evening Session (18:00 – 21:30 IST)**:
    - Perform 1-2 searches.
    - Harvest 80-120 cards.
    - Enrich 3-5 profiles.

### The "Batch & Break" Cycle (Inside a Session)
When running campaigns, the bot doesn't just run top-to-bottom. It follows this loop:
1.  **Work**: Process **3 to 4 profiles** (each with 30-75s gaps).
2.  **Distraction**: Take a **3 to 7 minute "Coffee Break"**.
3.  **Repeat**: Continue until the daily limit or session goal is hit.

---

## 4. Step-by-Step Workflow & Process

### Step 1: Operating Hours Check
Before any action, the system checks the time.
- **Active Window**: 09:30 – 21:30 IST (India Standard Time).
- **Behavior**: If triggered at 11 PM or 6 AM, the bot will immediately abort to prevent "24/7 Bot" fingerprinting.

### Step 2: Search & Discovery (Harvesting)
When you start a search:
1. **Pacing**: The bot waits **17 – 43 seconds** (randomized) between every page of search results.
2. **Scrolling**: It performs randomized mouse-wheel scrolls (800-1200px) and pauses (1.2 - 2.5s) to simulate reading.
3. **Limit Check**: If the daily "Card" or "Search" quota is hit mid-run, it stops immediately and saves progress.

### Step 3: Profile Enrichment (Deep Scraping)
When you launch a campaign or enrich candidates:
1. **Batching**: The bot processes candidates in small groups of **3 to 4**.
2. **Coffee Breaks**: After every batch, it takes a "Reading Break" of **3 to 7 minutes** (180 - 420 seconds).
3. **Profile-to-Profile Delay**: Between individual profiles in a batch, it waits **30 – 75 seconds**.
4. **Action Pacing**: Button clicks and UI steps have jittery delays (1.5 - 4.0s) so they aren't instant.

---

## 4. Human-Mimicry Techniques

| Technique | Description |
| :--- | :--- |
| **Deterministic Randomness** | Daily limits are calculated using your username + current date as a "seed." This means your limit is stable for the day but different from every other user and different every day of the week. |
| **Gaussian Distribution** | Delays aren't just `min` and `max`. We use "Bell Curve" math so most delays are in the middle, but we occasionally have very long or very short pauses, just like a human. |
| **Visual Interaction** | We use `playwright-stealth` to mask browser fingerprints and emulate realistic mouse movements and scrolling. |
| **Session Spikes** | We intentionally avoid "cleaning" the entire queue at once. The system encourages multiple small sessions throughout the day. |

---

## 5. Safety Failsafes

1. **The Sidebar Safety Guard**: The UI shows real-time progress bars. If you reach a limit, the "Start" buttons are disabled and turn grey.
2. **Backend Interceptor**: Every single page load or API call passes through a `check_safety()` function. If the count is exceeded (even if the UI is bypassed), the backend will refuse to execute the browser action.
3. **Persistence**: Usage is saved in a local `usage_stats.json`. Even if the server restarts, it remembers exactly how many actions were performed today and this month.
4. **Crash on Detection**: If LinkedIn shows a "Security Check" or "Verification" page, the bot is programmed to **stop immediately** and notify the user rather than trying to solve it and digging a deeper hole.

---

## 6. How to Build Reputation
For the first 14 days of using a new account:
* Keep enrichment below **5 per day**.
* Stick to the **100 cards/day** harvesting level.
* Use the **"Enrich Mode"** (No messages) to build a history of "passive browsing" before starting outreach.
