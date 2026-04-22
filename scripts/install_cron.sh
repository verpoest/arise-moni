#!/bin/bash
# Installs arise-moni cron jobs using paths from config/common.env.
# Safe to re-run: existing arise-moni entries are replaced, not duplicated.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG="$SCRIPT_DIR/../config/common.env"

if [ ! -f "$CONFIG" ]; then
    echo "Error: config not found at $CONFIG"
    exit 1
fi

source "$CONFIG"

if [ -z "$LOG_DIR" ]; then
    echo "Error: LOG_DIR is not set in $CONFIG"
    exit 1
fi

mkdir -p "$LOG_DIR"

NEW_ENTRIES="### ARISE MONI ###
# System health check every half hour
15,45 * * * * /bin/bash $SCRIPT_DIR/monitor_health.sh >> $LOG_DIR/health.log 2>&1

# Daily processing at 1:00 AM, followed by website update
0 1 * * * cd $SCRIPT_DIR/.. && python scripts/process_day.py >> $LOG_DIR/process.log 2>&1 && python scripts/update_web.py >> $LOG_DIR/web.log 2>&1

# Pull CHK microcontroller data every hour at minute 5
5 * * * * /bin/bash $SCRIPT_DIR/pull_chk_data.sh >> $LOG_DIR/chk_pull.log 2>&1"

# Remove any existing arise-moni block, then append the new one
(crontab -l 2>/dev/null | sed '/### ARISE MONI ###/,/^$/d'; echo "$NEW_ENTRIES"; echo "") | crontab -

echo "Cron jobs installed:"
echo "$NEW_ENTRIES"
