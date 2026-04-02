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

# fire_sentinel SENTINEL TYPE ENTITY "Message (no leading newline)"
# - Creates sentinel and queues new alert if this is the first occurrence.
# - Checks for 24h follow-up if sentinel already exists.
fire_sentinel() {
    local sentinel="$1" type="$2" entity="$3" msg="$4"
    if [ ! -f "$sentinel" ]; then
        touch "$sentinel"
        log_alert "$type" "$entity"
        ERROR_FOUND=1
        ERROR_MSG+=$'\n\n'"$msg"
    fi
    local followup="${sentinel}_24h"
    if [ -n "$(find "$sentinel" -mmin +1440 2>/dev/null)" ]; then
        if [ ! -f "$followup" ] || [ -n "$(find "$followup" -mmin +1440 2>/dev/null)" ]; then
            touch "$followup"
            FOLLOWUP_FOUND=1
            FOLLOWUP_MSG+=$'\n\n[STILL ONGOING 24h+] '"$msg"
        fi
    fi
}

# clear_sentinel SENTINEL TYPE ENTITY "Resolved description"
# - Removes sentinel and queues resolved notification if it was active.
clear_sentinel() {
    local sentinel="$1" type="$2" entity="$3" msg="$4"
    if [ -f "$sentinel" ]; then
        rm -f "$sentinel" "${sentinel}_24h"
        log_alert "resolved_${type}" "$entity"
        RESOLVED_FOUND=1
        RESOLVED_MSG+=$'\n\n[RESOLVED] '"$msg"
    else
        rm -f "${sentinel}_24h"
    fi
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
        # Escape for valid JSON: backslashes, double quotes, newlines
        msg="${msg//\\/\\\\}"
        msg="${msg//\"/\\\"}"
        msg="${msg//$'\n'/\\n}"
        curl -s -X POST -H 'Content-type: application/json' \
            --data "{\"message\": \"$msg\"}" \
            "$SLACK_WEBHOOK_URL" > /dev/null
    fi
}

# send_notification FOUND MSG SUBJECT LOG_MSG
send_notification() {
    local found="$1" msg="$2" subject="$3" log_msg="$4"
    if [ "$found" -eq 1 ]; then
        local mutt_output
        mutt_output=$(echo "$msg" | mutt -s "$subject" -- $RECIPIENT_LIST 2>&1)
        local mutt_exit=$?
        if [ $mutt_exit -ne 0 ]; then
            echo "[$(date -Iseconds)] EMAIL FAILED (exit $mutt_exit): $subject" >> "$LOG_DIR/mail_errors.log"
            echo "mutt output: $mutt_output" >> "$LOG_DIR/mail_errors.log"
            echo "ERROR: Failed to send email '$subject' (exit $mutt_exit). See $LOG_DIR/mail_errors.log" >&2
        else
            echo "[$(date -Iseconds)] Email sent: $subject -> $RECIPIENT_LIST" >> "$LOG_DIR/mail_errors.log"
        fi
        send_slack "$subject on $(hostname):\n$msg"
        echo "$log_msg"
    fi
}

# Flags
ERROR_FOUND=0
ERROR_MSG="WARNING: Issues detected on ARISE DAQ ($(hostname)):"
RESOLVED_FOUND=0
RESOLVED_MSG="Issues resolved on ARISE DAQ ($(hostname)):"
FOLLOWUP_FOUND=0
FOLLOWUP_MSG="Issues still ongoing after 24h on ARISE DAQ ($(hostname)):"

# ================= 0. SSD ACCESSIBILITY CHECK =================
SSD_SENTINEL="$ALERT_STATE_DIR/alert_ssd_io"
SSD_OK=1

timeout 10 ls "$DATA_DIR" > /dev/null 2>&1; _ls_exit=$?
if [ $_ls_exit -ne 0 ]; then
    SSD_OK=0
    [ $_ls_exit -eq 124 ] \
        && _ssd_msg="[SSD ERROR] Data directory is not responding (filesystem hung). Skipping file checks." \
        || _ssd_msg="[SSD ERROR] Data directory is not accessible (I/O error). Skipping file checks."
    fire_sentinel "$SSD_SENTINEL" ssd_io ssd "$_ssd_msg"
else
    clear_sentinel "$SSD_SENTINEL" ssd_io ssd \
        "Data directory (taxissd_3) is accessible again."
fi

# ================= 0b. OUTPUT SSD ACCESSIBILITY CHECK =================
OUTPUT_SSD_SENTINEL="$ALERT_STATE_DIR/alert_output_ssd_io"
OUTPUT_SSD_OK=1

timeout 10 ls "$OUTPUT_DIR" > /dev/null 2>&1; _ls_exit=$?
if [ $_ls_exit -ne 0 ]; then
    OUTPUT_SSD_OK=0
    [ $_ls_exit -eq 124 ] \
        && _ssd_msg="[SSD ERROR] Output directory is not responding (filesystem hung). Skipping output disk check." \
        || _ssd_msg="[SSD ERROR] Output directory is not accessible (I/O error). Skipping output disk check."
    fire_sentinel "$OUTPUT_SSD_SENTINEL" output_ssd_io output_ssd "$_ssd_msg"
else
    clear_sentinel "$OUTPUT_SSD_SENTINEL" output_ssd_io output_ssd \
        "Output directory (taxissd_2) is accessible again."
fi

# ================= 1. DISK CHECK =================
if [ $SSD_OK -eq 1 ]; then

DISK_USAGE=$(timeout 10 df "$DATA_DIR" 2>/dev/null | awk 'NR==2 {print $5}' | sed 's/%//')
DISK_SENTINEL="$ALERT_STATE_DIR/alert_disk"

if [[ "$DISK_USAGE" =~ ^[0-9]+$ ]] && [ "$DISK_USAGE" -gt "$DISK_THRESHOLD_PERCENT" ]; then
    fire_sentinel "$DISK_SENTINEL" disk disk \
        "[DISK FULL] Data drive ($DATA_DIR) is at $DISK_USAGE% capacity."
else
    clear_sentinel "$DISK_SENTINEL" disk disk \
        "Data drive ($DATA_DIR) disk usage is back below threshold (now at $DISK_USAGE%)."
fi

# ================= 2. STATION CHECKS =================
for i in {1..6}; do
    STATION="s$i"
    LIVE_SENTINEL="$ALERT_STATE_DIR/alert_${STATION}_live"
    SIZE_SENTINEL="$ALERT_STATE_DIR/alert_${STATION}_size"

    # Check if files exist (Liveness & Size)
    LIVE_CHECK=$(timeout 30 find "$DATA_DIR" -name "${STATION}_eventData_*.bin" -mmin -30 -size +0c -print -quit 2>/dev/null)
    SIZE_CHECK=$(timeout 30 find "$DATA_DIR" -name "${STATION}_eventData_*.bin" -mmin -120 -size +15G -print -quit 2>/dev/null)

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

    # Ping once per station, only when a new alert needs to fire
    _conn=""
    if { [ -z "$LIVE_CHECK" ] && [ ! -f "$LIVE_SENTINEL" ]; } || \
       { [ -z "$SIZE_CHECK" ] && [ ! -f "$SIZE_SENTINEL" ]; }; then
        _conn=$(_conn_status)
    fi

    # Liveness check
    if [ -z "$LIVE_CHECK" ]; then
        fire_sentinel "$LIVE_SENTINEL" live "s$i" \
            "[FAIL] Station $STATION: No data written in last 30 mins.$_conn"
    else
        clear_sentinel "$LIVE_SENTINEL" live "s$i" \
            "Station $STATION: Data is being written again."
    fi

    # File size check
    if [ -z "$SIZE_CHECK" ]; then
        fire_sentinel "$SIZE_SENTINEL" size "s$i" \
            "[FAIL] Station $STATION: No 15GB+ file generated in last 2 hours.$_conn"
    else
        clear_sentinel "$SIZE_SENTINEL" size "s$i" \
            "Station $STATION: Large file (15GB+) has been generated."
    fi
done

fi # end SSD_OK

# ================= 1b. OUTPUT DISK CHECK =================
if [ $OUTPUT_SSD_OK -eq 1 ]; then

OUTPUT_DISK_USAGE=$(timeout 10 df "$OUTPUT_DIR" 2>/dev/null | awk 'NR==2 {print $5}' | sed 's/%//')
OUTPUT_DISK_SENTINEL="$ALERT_STATE_DIR/alert_output_disk"

if [[ "$OUTPUT_DISK_USAGE" =~ ^[0-9]+$ ]] && [ "$OUTPUT_DISK_USAGE" -gt "$DISK_THRESHOLD_PERCENT" ]; then
    fire_sentinel "$OUTPUT_DISK_SENTINEL" output_disk output_disk \
        "[DISK FULL] Output drive ($OUTPUT_DIR) is at $OUTPUT_DISK_USAGE% capacity."
else
    clear_sentinel "$OUTPUT_DISK_SENTINEL" output_disk output_disk \
        "Output drive ($OUTPUT_DIR) disk usage is back below threshold (now at $OUTPUT_DISK_USAGE%)."
fi

fi # end OUTPUT_SSD_OK

# ================= 3. NOTIFICATION =================
RECIPIENT_LIST=$(echo "$EMAIL_RECIPIENTS" | tr ',' ' ')

send_notification $ERROR_FOUND    "$ERROR_MSG"    "ARISE MONI ALERT"            "New issues found. Alerts sent via mutt and Slack."
send_notification $RESOLVED_FOUND "$RESOLVED_MSG" "ARISE MONI RESOLVED"         "Issues resolved. Notifications sent via mutt and Slack."
send_notification $FOLLOWUP_FOUND "$FOLLOWUP_MSG" "ARISE MONI ALERT (24h ongoing)" "24h follow-up sent via mutt and Slack."

# ================= HEARTBEAT =================
HEARTBEAT_SENTINEL="$LOG_DIR/heartbeat_last_sent"
if [ -z "$(find "$HEARTBEAT_SENTINEL" -mmin -1440 2>/dev/null)" ]; then
    touch "$HEARTBEAT_SENTINEL"
    if compgen -G "$ALERT_STATE_DIR/alert_*" > /dev/null 2>&1; then
        HEARTBEAT_BODY="ARISE monitoring is running on $(hostname) as of $(date). There are active issues — see recent alert emails for details."
    else
        HEARTBEAT_BODY="ARISE monitoring is running on $(hostname) as of $(date). All systems healthy."
    fi
    heartbeat_out=$(echo "$HEARTBEAT_BODY" | mutt -s "ARISE MONI HEARTBEAT" -- $RECIPIENT_LIST 2>&1)
    heartbeat_exit=$?
    if [ $heartbeat_exit -ne 0 ]; then
        echo "[$(date -Iseconds)] HEARTBEAT FAILED (exit $heartbeat_exit): $heartbeat_out" >> "$LOG_DIR/mail_errors.log"
        echo "ERROR: Failed to send heartbeat email (exit $heartbeat_exit). See $LOG_DIR/mail_errors.log" >&2
    else
        echo "[$(date -Iseconds)] Heartbeat sent -> $RECIPIENT_LIST" >> "$LOG_DIR/mail_errors.log"
        echo "Daily heartbeat sent."
    fi
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
            alert_*_24h)         ;;  # internal marker, skip display
            *)                   echo "  [UNKNOWN]   $name" ;;
        esac
    done
elif [ $ERROR_FOUND -eq 0 ] && [ $RESOLVED_FOUND -eq 0 ]; then
    echo "System healthy."
fi
