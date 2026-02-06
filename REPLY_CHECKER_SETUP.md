# LinkedIn Reply Checker - Setup Guide

## 🎯 What This Does
Automatically checks all your COMPLETED LinkedIn profiles every 30 minutes to see if they've replied to your messages.

## 📋 Files Created
1. **`check_replies.py`** - Main script that scans for new replies
2. **`run_reply_checker.sh`** - Shell script that runs the checker every 30 minutes
3. **`com.linkedin.replychecker.plist`** - macOS LaunchAgent for auto-start (optional)

## 🚀 Quick Start

### Option 1: Manual Start (Recommended for Testing)
```bash
# Run once to test
./venv/bin/python check_replies.py

# Run continuously (every 30 minutes)
./run_reply_checker.sh
```

### Option 2: Auto-Start on Mac Boot (Background Service)
```bash
# Copy the plist to LaunchAgents
cp com.linkedin.replychecker.plist ~/Library/LaunchAgents/

# Load the service (starts immediately and on every boot)
launchctl load ~/Library/LaunchAgents/com.linkedin.replychecker.plist

# Check if it's running
launchctl list | grep replychecker

# View logs
tail -f logs/reply_checker.log

# Stop the service
launchctl unload ~/Library/LaunchAgents/com.linkedin.replychecker.plist
```

## 📊 How It Works
1. Queries your database for all profiles in `COMPLETED` state (those you've sent messages to)
2. Opens each profile's LinkedIn chat
3. Extracts the latest messages
4. Identifies replies from the candidate (not from you)
5. Saves new replies to the database with timestamps
6. Updates the UI automatically (refresh the Pool view to see the blue "REPLY" badge)

## ⚙️ Configuration
- **Check Interval**: Edit `run_reply_checker.sh` and change `sleep 1800` (1800 seconds = 30 minutes)
- **Message Limit**: Edit `check_replies.py` line 73: `fetch_latest_messages(handle, profile, limit=10)`

## 🔍 Monitoring
```bash
# Watch live logs
tail -f logs/reply_checker.log

# Check for errors
tail -f logs/reply_checker_error.log
```

## 🛑 Stopping
```bash
# If running manually: Press Ctrl+C

# If running as service:
launchctl unload ~/Library/LaunchAgents/com.linkedin.replychecker.plist
```

## 💡 Tips
- The checker uses your existing browser session, so make sure you're logged into LinkedIn
- It adds a 2-4 second delay between each profile check to be polite to LinkedIn
- New replies appear in the UI with a blue "REPLY" badge - hover to see the message
- The checker is smart: it only logs NEW replies (won't duplicate existing ones)

## 🐛 Troubleshooting
**No replies detected?**
- Check `logs/reply_checker.log` for errors
- Make sure the profile is in `COMPLETED` state (message was sent)
- Verify you're logged into LinkedIn in the browser

**Service not starting?**
- Check permissions: `ls -la ~/Library/LaunchAgents/com.linkedin.replychecker.plist`
- View system logs: `log show --predicate 'subsystem == "com.apple.launchd"' --last 1h`

## 📝 Example Output
```
🔍 Checking replies for @vishnu02.tech@gmail.com...
Found 3 completed profiles to check.
Checking Priyanshu Kumar (priyanshu-kumar-a21b09229)...
📩 NEW REPLY from Priyanshu Kumar: Hi! Thanks for reaching out. I'm definitely interested...
✅ Checked 3 profiles. Found 1 new replies.
🎉 Reply check complete!
💤 Sleeping for 30 minutes...
```
