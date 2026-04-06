# 🎉 Automated Reply Tracking - Complete Setup

## ✅ What's Been Built

You now have a **fully automated reply tracking system** that monitors all your LinkedIn outreach and captures candidate responses!

### 🔧 Components Created

1. **Database Schema** (`linkedin/db/models.py`)
   - `last_received_message` - Stores the candidate's reply text
   - `last_received_at` - Timestamp of when they replied
   - Auto-migrates existing databases

2. **Chat Scraper** (`linkedin/actions/chat.py`)
   - Opens LinkedIn messaging for each candidate
   - Extracts conversation history
   - Identifies which messages are from candidates vs you
   - Multiple fallback strategies for reliability

3. **Database Helper** (`linkedin/db/profiles.py`)
   - `save_received_message()` - Logs incoming messages
   - Smart duplicate detection

4. **Automated Checker** (`check_replies.py`)
   - Scans all COMPLETED profiles
   - Runs every 30 minutes (configurable)
   - Logs all activity to `logs/reply_checker.log`

5. **Background Service** (`run_reply_checker.sh`)
   - Continuous monitoring
   - Auto-restart on failure
   - macOS LaunchAgent support

6. **UI Integration**
   - Blue "REPLY" badge appears when candidates respond
   - Hover to see full message + timestamp
   - "CHECK REPLIES" button for manual checks
   - Auto-refresh after checking

7. **API Endpoint** (`/api/check_replies`)
   - Trigger reply checks from UI
   - Streams logs to terminal output

---

## 🚀 How to Use

### Option 1: Automated (Every 30 Minutes)
```bash
# Start the background checker
./run_reply_checker.sh

# It will run continuously, checking every 30 minutes
# Press Ctrl+C to stop
```

### Option 2: Manual (From UI)
1. Go to **Candidate Pool** tab
2. Click **CHECK REPLIES** button
3. Watch the Terminal Logs for progress
4. Results auto-refresh after 30 seconds

### Option 3: One-Time Check
```bash
./venv/bin/python check_replies.py
```

---

## 📊 How It Works

```
Every 30 minutes (or when you click "CHECK REPLIES"):
  ↓
1. Query database for all COMPLETED profiles
  ↓
2. For each profile:
   - Open their LinkedIn chat
   - Extract last 10 messages
   - Find latest message from THEM (not you)
  ↓
3. If new reply detected:
   - Save to database with timestamp
   - Update UI (blue REPLY badge appears)
  ↓
4. Continue to next profile
  ↓
5. Log summary: "Checked X profiles, found Y new replies"
```

---

## 🎨 UI Features

### Candidate Pool View
- **SENT Badge** (Green) - You sent them a message
- **REPLY Badge** (Blue) - They replied to you!
- **Hover tooltips** - See full message content + timestamp
- **Status filters** - Filter by Pending, Connected, Completed, etc.

### Example Display
```
┌─────────────────────────────────────┐
│ Priyanshu Kumar                     │
│ Backend Developer                   │
│                                     │
│ Status: COMPLETED                   │
│ [✉️ SENT] [💬 REPLY]               │
│                                     │
│ Hover over REPLY to see:           │
│ "Hi! Thanks for reaching out.      │
│  I'm definitely interested..."      │
│ Timestamp: 2026-02-05 20:45:23     │
└─────────────────────────────────────┘
```

---

## ⚙️ Configuration

### Change Check Interval
Edit `run_reply_checker.sh`:
```bash
sleep 1800  # 30 minutes (1800 seconds)
# Change to 3600 for 1 hour, 900 for 15 minutes, etc.
```

### Change Message Limit
Edit `check_replies.py` line 73:
```python
messages = fetch_latest_messages(handle, profile, limit=10)
# Increase to 20, 50, etc. to scan more message history
```

### Auto-Start on Mac Boot
```bash
# Install as system service
cp com.linkedin.replychecker.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.linkedin.replychecker.plist

# Check if running
launchctl list | grep replychecker

# Stop service
launchctl unload ~/Library/LaunchAgents/com.linkedin.replychecker.plist
```

---

## 📝 Logs & Monitoring

### View Live Logs
```bash
tail -f logs/reply_checker.log
```

### Example Log Output
```
21:00:23 │ INFO │ 🔍 Checking replies for @vishnu02.tech@gmail.com...
21:00:23 │ INFO │ Found 2 completed profiles to check.
21:00:39 │ INFO │ Checking Priyanshu Kumar (priyanshu-kumar-a21b09229)...
21:01:05 │ INFO │ Found 8 messages for priyanshu-kumar-a21b09229
21:01:05 │ INFO │ 📩 NEW REPLY from Priyanshu Kumar: Hi! Thanks for...
21:01:10 │ INFO │ ✅ Checked 2 profiles. Found 1 new replies.
21:01:10 │ INFO │ 🎉 Reply check complete!
```

---

## 🐛 Troubleshooting

### "No messages found"
- The candidate might not have replied yet
- Check if you're logged into LinkedIn in the browser
- Verify the profile is in COMPLETED state

### "Timeout errors"
- LinkedIn UI might have changed
- Try running manually first: `./venv/bin/python check_replies.py`
- Check logs for specific error messages

### "Another process is running"
- Stop the current campaign/harvest first
- Or wait for it to complete

### Database not updating
- Check `logs/reply_checker_error.log`
- Verify database permissions
- Try: `sqlite3 assets/data/vishnu02.tech@gmail.com.db "SELECT * FROM profiles WHERE last_received_message IS NOT NULL;"`

---

## 💡 Pro Tips

1. **Run during off-hours** - Set up the LaunchAgent to avoid conflicts with active campaigns
2. **Check after campaigns** - Manually trigger after sending messages to catch quick replies
3. **Monitor the logs** - Keep an eye on `logs/reply_checker.log` for patterns
4. **Adjust timing** - If you're getting rate-limited, increase the check interval
5. **Filter by COMPLETED** - In the UI, filter by "Completed" status to see who you've messaged

---

## 📈 Next Steps

Want to enhance this further? Here are some ideas:

- **Email notifications** when new replies arrive
- **Auto-categorize replies** (interested, not interested, questions)
- **Reply sentiment analysis** using AI
- **Auto-respond** to common questions
- **Slack/Discord webhooks** for instant notifications

---

## 🎯 Summary

You now have:
✅ Automated reply checking every 30 minutes
✅ Manual "CHECK REPLIES" button in UI
✅ Blue REPLY badges with hover tooltips
✅ Complete message history tracking
✅ Background service support
✅ Comprehensive logging

**Your outreach is now fully tracked from send to reply!** 🚀⚓️
