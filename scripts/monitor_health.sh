#!/bin/bash

# Load Central Config
source "$(dirname "$0")/../config/common.env"

# State directory for alert deduplication (sentinel files)
ALERT_STATE_DIR="$LOG_DIR/alert_state"
mkdir -p "$ALERT_STATE_DIR"

# Alert history CSV log
ALERT_HISTORY="$LOG_DIR/alert_history.csv"

# Append one row to alert_history.csv when a new sentinel fires
log_alert() {
    local type="$1" entity="$2"
    if [ ! -f "$ALERT_HISTORY" ]; then
        echo "timestamp,type,entity" > "$ALERT_HISTORY"
    fi
    echo "$(date -Iseconds),$type,$entity" >> "$ALERT_HISTORY"
}

# Check that email recipients are configured before doing anything else
if [ -z "$EMAIL_RECIPIENTS" ]; then
    echo "ERROR: EMAIL_RECIPIENTS is not set in config. Cannot send alerts. Exiting."
    exit 1
fi

# Slack helper — only sends if SLACK_WEBHOOK_URL is configured
send_slack() {
    local msg="$1"
    if [ -n "$SLACK_WEBHOOK_URL" ]; then
        curl -s -X POST -H 'Content-type: application/json' \
            --data "{\"text\": \"$msg\"}" \
            "$SLACK_WEBHOOK_URL" > /dev/null
    fi
}

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
        log_alert ssd_io ssd
        ERROR_FOUND=1
        ERROR_MSG+=$'\n\n[SSD ERROR] Data directory is not accessible (Input/output error). Skipping file checks.'
    fi
else
    rm -f "$SSD_SENTINEL"
fi

# ================= 0b. OUTPUT SSD ACCESSIBILITY CHECK =================
OUTPUT_SSD_SENTINEL="$ALERT_STATE_DIR/alert_output_ssd_io"
OUTPUT_SSD_OK=1

if ls "$OUTPUT_DIR" 2>&1 | grep -q "Input/output error"; then
    OUTPUT_SSD_OK=0
    if [ ! -f "$OUTPUT_SSD_SENTINEL" ]; then
        touch "$OUTPUT_SSD_SENTINEL"
        log_alert output_ssd_io output_ssd
        ERROR_FOUND=1
        ERROR_MSG+=$'\n\n[SSD ERROR] Output directory is not accessible (Input/output error). Skipping output disk check.'
    fi
else
    rm -f "$OUTPUT_SSD_SENTINEL"
fi

# ================= 1. DISK CHECK =================
if [ $SSD_OK -eq 1 ]; then

DISK_USAGE=$(df "$DATA_DIR" 2>/dev/null | awk 'NR==2 {print $5}' | sed 's/%//')
DISK_SENTINEL="$ALERT_STATE_DIR/alert_disk"

if [[ "$DISK_USAGE" =~ ^[0-9]+$ ]] && [ "$DISK_USAGE" -gt "$DISK_THRESHOLD_PERCENT" ]; then
    if [ ! -f "$DISK_SENTINEL" ]; then
        touch "$DISK_SENTINEL"
        log_alert disk disk
        ERROR_FOUND=1
        ERROR_MSG+=$'\n\n[DISK FULL] Data drive is at '"$DISK_USAGE"'% capacity.'
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
            log_alert live "s$i"
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
            log_alert size "s$i"
            ERROR_FOUND=1
            ERROR_MSG+=$'\n[FAIL] Station '"$STATION"': No 15GB+ file generated in last 2 hours.'"$(_conn_status)"
        fi
    else
        rm -f "$SIZE_SENTINEL"
    fi
done

fi # end SSD_OK

# ================= 1b. OUTPUT DISK CHECK =================
if [ $OUTPUT_SSD_OK -eq 1 ]; then

OUTPUT_DISK_USAGE=$(df "$OUTPUT_DIR" 2>/dev/null | awk 'NR==2 {print $5}' | sed 's/%//')
OUTPUT_DISK_SENTINEL="$ALERT_STATE_DIR/alert_output_disk"

if [[ "$OUTPUT_DISK_USAGE" =~ ^[0-9]+$ ]] && [ "$OUTPUT_DISK_USAGE" -gt "$DISK_THRESHOLD_PERCENT" ]; then
    if [ ! -f "$OUTPUT_DISK_SENTINEL" ]; then
        touch "$OUTPUT_DISK_SENTINEL"
        log_alert output_disk output_disk
        ERROR_FOUND=1
        ERROR_MSG+=$'\n\n[DISK FULL] Output drive is at '"$OUTPUT_DISK_USAGE"'% capacity.'
    fi
else
    rm -f "$OUTPUT_DISK_SENTINEL"
fi

fi # end OUTPUT_SSD_OK

# ================= 3. NOTIFICATION (MUTT) =================
if [ $ERROR_FOUND -eq 1 ]; then
    # Convert "email1,email2" from config into "email1 email2" for mutt arguments
    RECIPIENT_LIST=$(echo "$EMAIL_RECIPIENTS" | tr ',' ' ')

    # Send via mutt
    # The '--' ensures that addresses starting with - aren't read as flags
    echo "$ERROR_MSG" | mutt -s "ARISE MONI ALERT" -- $RECIPIENT_LIST
    send_slack ":rotating_light: *ARISE MONI ALERT* on $(hostname):\n$ERROR_MSG"

    echo "New issues found. Alerts sent via mutt and Slack."
fi

# ================= 4. STATUS SUMMARY =================
ACTIVE_SENTINELS=( "$ALERT_STATE_DIR"/alert_* )

if [ -f "${ACTIVE_SENTINELS[0]}" ]; then
    echo "Ongoing known issues (alert already sent, no repeat email):"
    for sentinel in "${ACTIVE_SENTINELS[@]}"; do
        name=$(basename "$sentinel")
        case "$name" in
            alert_ssd_io)        echo "  [SSD ERROR] Data directory not accessible" ;;
            alert_disk)          echo "  [DISK FULL] Data drive usage above threshold" ;;
            alert_output_ssd_io) echo "  [SSD ERROR] Output directory not accessible" ;;
            alert_output_disk)   echo "  [DISK FULL] Output drive usage above threshold" ;;
            alert_s*_live)       echo "  [NO DATA]   Station ${name#alert_}: no data written in last 30 mins" ;;
            alert_s*_size)       echo "  [SIZE]      Station ${name#alert_}: no 15GB+ file in last 2 hours" ;;
            *)                   echo "  [UNKNOWN]   $name" ;;
        esac
    done
elif [ $ERROR_FOUND -eq 0 ]; then
    echo "System healthy."
fi
