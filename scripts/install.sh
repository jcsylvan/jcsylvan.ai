#!/usr/bin/env bash
# install.sh — Set up the arXiv Popularity Tracker cron job
#
# Usage: ./install.sh
#
# This installs a cron job that:
#   - Runs arxiv_tracker.py at 3:30 AM ET daily (fetches papers + scores them)
#   - Adjusts for ET automatically via the TZ environment variable
#
# Prerequisites:
#   1. Copy config.example.ini to config.ini and fill in your credentials
#   2. pip install -r requirements.txt

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$(command -v python3)"

if [ -z "$PYTHON" ]; then
    echo "Error: python3 not found. Please install Python 3.10+."
    exit 1
fi

if [ ! -f "$SCRIPT_DIR/config.ini" ]; then
    echo "Error: config.ini not found."
    echo "Copy config.example.ini to config.ini and fill in your credentials."
    exit 1
fi

# Install Python dependencies
echo "Installing Python dependencies..."
"$PYTHON" -m pip install -r "$SCRIPT_DIR/requirements.txt" --quiet

# Build cron entry
# TZ=America/New_York ensures 3:30 AM Eastern (handles DST automatically)
CRON_ENTRY="TZ=America/New_York
30 3 * * * $PYTHON $SCRIPT_DIR/arxiv_tracker.py >> $SCRIPT_DIR/arxiv_tracker.log 2>&1"

# Check if cron job already exists
if crontab -l 2>/dev/null | grep -qF "arxiv_tracker.py"; then
    echo "Cron job already exists. Updating..."
    crontab -l 2>/dev/null | grep -vF "arxiv_tracker.py" | grep -v "^TZ=America/New_York$" > /tmp/crontab_tmp || true
    echo "$CRON_ENTRY" >> /tmp/crontab_tmp
    crontab /tmp/crontab_tmp
    rm /tmp/crontab_tmp
else
    echo "Installing new cron job..."
    (crontab -l 2>/dev/null || true; echo ""; echo "$CRON_ENTRY") | crontab -
fi

echo ""
echo "Cron job installed. The tracker will run daily at 3:30 AM Eastern Time."
echo "Logs: $SCRIPT_DIR/arxiv_tracker.log"
echo ""
echo "To test now:  $PYTHON $SCRIPT_DIR/arxiv_tracker.py"
echo "To view cron: crontab -l"
