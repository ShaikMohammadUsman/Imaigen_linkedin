#!/bin/bash
# run_reply_checker.sh
# Runs the reply checker every 30 minutes

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

echo "🚀 Starting LinkedIn Reply Checker (every 30 minutes)..."
echo "Press Ctrl+C to stop."
echo ""

while true; do
    echo "⏰ $(date '+%Y-%m-%d %H:%M:%S') - Checking for new replies..."
    ./venv/bin/python check_replies.py
    echo ""
    echo "💤 Sleeping for 30 minutes..."
    echo ""
    sleep 1800  # 30 minutes = 1800 seconds
done
