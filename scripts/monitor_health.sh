#!/bin/bash

# Load Central Config
source "$(dirname "$0")/../config/common.env"

# State directory for alert deduplication (sentinel files)
ALERT_STATE_DIR="$LOG_DIR/alert_state"
mkdir -p "$ALERT_STATE_DIR"

# Check that email recipients are configured before doing anything else
if [ -z "$EMAIL_RECIPIENTS" ]; then
    echo "ERROR: EMAIL_RECIPIENTS is not set in config. Cannot send alerts. Exiting."
    exit 1
fi

# Flags
ERROR_FOUND=0
ERROR_MSG="WARNING: Issues detected on ARISE DAQ ($(hostname)):"

# ================= 0. SSD ACCESSIBILITY CHECK =================
SSD_SENTINEL="$ALERT_STATE_DIR/alert_ssd_io"
SSD_OK=1

if ls "$DATA_DIR" 2>&1 | grep -q "Input/output error"; then
    SSD_OK=0
    if [ ! -f "$SSD_SENTINEL" ]; then
        touch "$SSD_SENTINEL"
        ERROR_FOUND=1
        ERROR_MSG+=$'\n\n[SSD ERROR] Data directory is not accessible (Input/output error). Skipping file checks.'
    fi
else
    rm -f "$SSD_SENTINEL"
fi

# ================= 1. DISK CHECK =================
if [ $SSD_OK -eq 1 ]; then

DISK_USAGE=$(df "$DATA_DIR" 2>/dev/null | awk 'NR==2 {print $5}' | sed 's/%//')
DISK_SENTINEL="$ALERT_STATE_DIR/alert_disk"

if [[ "$DISK_USAGE" =~ ^[0-9]+$ ]] && [ "$DISK_USAGE" -gt "$DISK_THRESHOLD_PERCENT" ]; then
    if [ ! -f "$DISK_SENTINEL" ]; then
        touch "$DISK_SENTINEL"
        ERROR_FOUND=1
        ERROR_MSG+=$'\n\n[DISK FULL] Drive is at '"$DISK_USAGE"'% capacity.'
    fi
else
    rm -f "$DISK_SENTINEL"
fi

# ================= 2. STATION CHECKS =================
for i in {1..6}; do
    STATION="s$i"
    LIVE_SENTINEL="$ALERT_STATE_DIR/alert_${STATION}_live"
    SIZE_SENTINEL="$ALERT_STATE_DIR/alert_${STATION}_size"

    # 1. Check if files exist (Liveness & Size)
    LIVE_CHECK=$(find "$DATA_DIR" -name "${STATION}_eventData_*.bin" -mmin -30 -size +0c | head -n 1)
    SIZE_CHECK=$(find "$DATA_DIR" -name "${STATION}_eventData_*.bin" -mmin -120 -size +15G | head -n 1)

    # Helper: check network connectivity (only called when generating a new alert)
    _conn_status() {
        local IP_VAR="STATION_IP_$i"
        local TARGET_IP="${!IP_VAR}"
        if [ -z "$TARGET_IP" ]; then
            echo " (IP not configured)"
        elif ping -c 1 -W 5 "$TARGET_IP" &> /dev/null; then
            echo " [NETWORK OK]"
        else
            echo " [NETWORK UNREACHABLE]"
        fi
    }

    # Liveness check
    if [ -z "$LIVE_CHECK" ]; then
        if [ ! -f "$LIVE_SENTINEL" ]; then
            touch "$LIVE_SENTINEL"
            ERROR_FOUND=1
            ERROR_MSG+=$'\n[FAIL] Station '"$STATION"': No data written in last 30 mins.'"$(_conn_status)"
        fi
    else
        rm -f "$LIVE_SENTINEL"
    fi

    # File size check
    if [ -z "$SIZE_CHECK" ]; then
        if [ ! -f "$SIZE_SENTINEL" ]; then
            touch "$SIZE_SENTINEL"
            ERROR_FOUND=1
            ERROR_MSG+=$'\n[FAIL] Station '"$STATION"': No 15GB+ file generated in last 2 hours.'"$(_conn_status)"
        fi
    else
        rm -f "$SIZE_SENTINEL"
    fi
done

fi # end SSD_OK

# ================= 3. NOTIFICATION (MUTT) =================
if [ $ERROR_FOUND -eq 1 ]; then
    # Convert "email1,email2" from config into "email1 email2" for mutt arguments
    RECIPIENT_LIST=$(echo "$EMAIL_RECIPIENTS" | tr ',' ' ')

    # Send via mutt
    # The '--' ensures that addresses starting with - aren't read as flags
    echo "$ERROR_MSG" | mutt -s "ARISE MONI ALERT" -- $RECIPIENT_LIST

    echo "New issues found. Alerts sent via mutt."
else
    echo "System healthy (no new alerts)."
fi
