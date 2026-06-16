#!/bin/bash

# Load Central Config
source "$(dirname "$0")/../config/common.env"

# State directory for alert deduplication (sentinel files)
ALERT_STATE_DIR="$LOG_DIR/alert_state"
mkdir -p "$ALERT_STATE_DIR"

# Alert history CSV log
ALERT_HISTORY="$LOG_DIR/alert_history.csv"

# Shared alert plumbing: log_alert, fire_sentinel, clear_sentinel,
# send_slack, send_notification
source "$(dirname "$0")/alert_lib.sh"

# Check that email recipients are configured before doing anything else
if [ -z "$EMAIL_RECIPIENTS" ]; then
    echo "ERROR: EMAIL_RECIPIENTS is not set in config. Cannot send alerts. Exiting."
    exit 1
fi

# Flags
ERROR_FOUND=0
ERROR_MSG="WARNING: Issues detected on ARISE DAQ ($(hostname)):
Timestamp: $(date)"
RESOLVED_FOUND=0
RESOLVED_MSG="Issues resolved on ARISE DAQ ($(hostname)):
Timestamp: $(date)"
FOLLOWUP_FOUND=0
FOLLOWUP_MSG="Issues still ongoing after 24h on ARISE DAQ ($(hostname)):
Timestamp: $(date)"

# ================= 0. SSD ACCESSIBILITY CHECK =================
SSD_SENTINEL="$ALERT_STATE_DIR/alert_ssd_io"
SSD_OK=1

timeout 10 ls "$DATA_DIR" > /dev/null 2>&1; _ls_exit=$?
if [ $_ls_exit -ne 0 ]; then
    SSD_OK=0
    [ $_ls_exit -eq 124 ] \
        && _ssd_msg="[SSD ERROR] Data directory is not responding (filesystem hung). Skipping file checks." \
        || _ssd_msg="[SSD ERROR] Data directory is not accessible (I/O error). Skipping file checks."
    fire_sentinel "$SSD_SENTINEL" ssd_io DATA_DISK "$_ssd_msg"
else
    clear_sentinel "$SSD_SENTINEL" ssd_io DATA_DISK \
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
    fire_sentinel "$OUTPUT_SSD_SENTINEL" output_ssd_io OUTPUT_DISK "$_ssd_msg"
else
    clear_sentinel "$OUTPUT_SSD_SENTINEL" output_ssd_io OUTPUT_DISK \
        "Output directory (taxissd_2) is accessible again."
fi

# ================= 1. DISK CHECK =================
if [ $SSD_OK -eq 1 ]; then

DISK_USAGE=$(timeout 10 df "$DATA_DIR" 2>/dev/null | awk 'NR==2 {print $5}' | sed 's/%//')
DISK_SENTINEL="$ALERT_STATE_DIR/alert_disk"

if [[ "$DISK_USAGE" =~ ^[0-9]+$ ]] && [ "$DISK_USAGE" -gt "$DISK_THRESHOLD_PERCENT" ]; then
    fire_sentinel "$DISK_SENTINEL" disk DATA_DISK_FULL \
        "[DISK FULL] Data drive ($DATA_DIR) is at $DISK_USAGE% capacity."
else
    clear_sentinel "$DISK_SENTINEL" disk DATA_DISK_FULL \
        "Data drive ($DATA_DIR) disk usage is back below threshold (now at $DISK_USAGE%)."
fi

# ================= 2. STATION CHECKS =================
for i in {1..6}; do
    STATION="s$i"
    WRLEN_SENTINEL="$ALERT_STATE_DIR/alert_${STATION}_wrlen"
    TAXI_SENTINEL="$ALERT_STATE_DIR/alert_${STATION}_taxi"
    LIVE_SENTINEL="$ALERT_STATE_DIR/alert_${STATION}_live"
    SIZE_SENTINEL="$ALERT_STATE_DIR/alert_${STATION}_size"

    # --- Layer 1: WR-LEN switch ---
    WRLEN_IP_VAR="WRLEN_IP_$i"
    WRLEN_IP="${!WRLEN_IP_VAR}"

    if [ -z "$WRLEN_IP" ]; then
        WRLEN_OK=1
    elif ping -c 1 -W 5 "$WRLEN_IP" &> /dev/null; then
        clear_sentinel "$WRLEN_SENTINEL" wrlen "$STATION" \
            "Station $STATION: WR-LEN switch ($WRLEN_IP) is reachable again."
        WRLEN_OK=1
    else
        fire_sentinel "$WRLEN_SENTINEL" wrlen "$STATION" \
            "[FAIL] Station $STATION: WR-LEN switch ($WRLEN_IP) is unreachable."
        WRLEN_OK=0
    fi

    # --- Layer 2: TAXI DAQ (only if WR-LEN is OK) ---
    if [ $WRLEN_OK -eq 1 ]; then
        TAXI_IP_VAR="TAXI_IP_$i"
        TAXI_IP="${!TAXI_IP_VAR}"

        if [ -z "$TAXI_IP" ]; then
            TAXI_OK=1
        elif ping -c 1 -W 5 "$TAXI_IP" &> /dev/null; then
            clear_sentinel "$TAXI_SENTINEL" taxi "$STATION" \
                "Station $STATION: TAXI DAQ ($TAXI_IP) is reachable again."
            TAXI_OK=1
        else
            fire_sentinel "$TAXI_SENTINEL" taxi "$STATION" \
                "[FAIL] Station $STATION: TAXI DAQ ($TAXI_IP) is unreachable (WR-LEN OK)."
            TAXI_OK=0
        fi
    else
        TAXI_OK=0
    fi

    # --- Layer 3: Data checks (only if both network layers are OK) ---
    if [ $WRLEN_OK -eq 1 ] && [ $TAXI_OK -eq 1 ]; then
        LIVE_CHECK=$(timeout 30 find "$DATA_DIR" -name "${STATION}_eventData_*.bin" \
            -mmin -30 -size +0c -print -quit 2>/dev/null)
        SIZE_CHECK=$(timeout 30 find "$DATA_DIR" -name "${STATION}_eventData_*.bin" \
            -mmin -120 -size +15G -print -quit 2>/dev/null)

        if [ -z "$LIVE_CHECK" ]; then
            fire_sentinel "$LIVE_SENTINEL" live "$STATION" \
                "[FAIL] Station $STATION: No data written in last 30 mins (network OK)."
        else
            clear_sentinel "$LIVE_SENTINEL" live "$STATION" \
                "Station $STATION: Data is being written again."
        fi

        if [ -z "$SIZE_CHECK" ]; then
            fire_sentinel "$SIZE_SENTINEL" size "$STATION" \
                "[FAIL] Station $STATION: No 15GB+ file generated in last 2 hours (network OK)."
        else
            clear_sentinel "$SIZE_SENTINEL" size "$STATION" \
                "Station $STATION: Large file (15GB+) has been generated."
        fi
    fi
done

fi # end SSD_OK

# ================= 1b. OUTPUT DISK CHECK =================
if [ $OUTPUT_SSD_OK -eq 1 ]; then

OUTPUT_DISK_USAGE=$(timeout 10 df "$OUTPUT_DIR" 2>/dev/null | awk 'NR==2 {print $5}' | sed 's/%//')
OUTPUT_DISK_SENTINEL="$ALERT_STATE_DIR/alert_output_disk"

if [[ "$OUTPUT_DISK_USAGE" =~ ^[0-9]+$ ]] && [ "$OUTPUT_DISK_USAGE" -gt "$DISK_THRESHOLD_PERCENT" ]; then
    fire_sentinel "$OUTPUT_DISK_SENTINEL" output_disk OUTPUT_DISK_FULL \
        "[DISK FULL] Output drive ($OUTPUT_DIR) is at $OUTPUT_DISK_USAGE% capacity."
else
    clear_sentinel "$OUTPUT_DISK_SENTINEL" output_disk OUTPUT_DISK_FULL \
        "Output drive ($OUTPUT_DIR) disk usage is back below threshold (now at $OUTPUT_DISK_USAGE%)."
fi

fi # end OUTPUT_SSD_OK

# ================= 2b. CHK BOX CHECKS =================
for i in {1..6}; do
    CHK_IP_VAR="ARISE_CHK_ST${i}_IP"
    CHK_IP="${!CHK_IP_VAR}"
    CHK_SENTINEL="$ALERT_STATE_DIR/alert_chk${i}"
    CHK_ESC_SENTINEL="$ALERT_STATE_DIR/alert_chk${i}_escalate"

    if [ -z "$CHK_IP" ]; then
        continue
    elif ping -c 1 -W 5 "$CHK_IP" &> /dev/null; then
        clear_sentinel "$CHK_SENTINEL" chk "chk${i}" \
            "CHK box $i ($CHK_IP) is reachable again." silent
        clear_sentinel "$CHK_ESC_SENTINEL" chk_escalate "chk${i}" \
            "CHK box $i ($CHK_IP) is reachable again."
    else
        fire_sentinel "$CHK_SENTINEL" chk "chk${i}" \
            "[FAIL] CHK box $i ($CHK_IP) is unreachable." silent
        # Escalate to email alert if unreachable for over 12 hours
        if [ -n "$(find "$CHK_SENTINEL" -mmin +720 2>/dev/null)" ]; then
            fire_sentinel "$CHK_ESC_SENTINEL" chk_escalate "chk${i}" \
                "[FAIL] CHK box $i ($CHK_IP) has been unreachable for over 12 hours."
        fi
    fi
done

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
        HEARTBEAT_BODY="ARISE monitoring is running on $(hostname) as of $(date). Active issues:"
        for sentinel in "$ALERT_STATE_DIR"/alert_*; do
            name=$(basename "$sentinel")
            case "$name" in
                alert_ssd_io)        HEARTBEAT_BODY+=$'\n'"  [SSD ERROR] Data directory not accessible" ;;
                alert_disk)          HEARTBEAT_BODY+=$'\n'"  [DISK FULL] Data drive usage above threshold" ;;
                alert_output_ssd_io) HEARTBEAT_BODY+=$'\n'"  [SSD ERROR] Output directory not accessible" ;;
                alert_output_disk)   HEARTBEAT_BODY+=$'\n'"  [DISK FULL] Output drive usage above threshold" ;;
                alert_chk*_escalate) _n="${name#alert_chk}"; HEARTBEAT_BODY+=$'\n'"  [CHK 12h+]  CHK box ${_n%_escalate}: unreachable 12+ hours" ;;
                alert_chk*)          ;;
                alert_s*_wrlen)      HEARTBEAT_BODY+=$'\n'"  [WRLEN]     Station ${name#alert_}: WR-LEN switch unreachable" ;;
                alert_s*_taxi)       HEARTBEAT_BODY+=$'\n'"  [TAXI]      Station ${name#alert_}: TAXI DAQ unreachable" ;;
                alert_s*_live)       HEARTBEAT_BODY+=$'\n'"  [NO DATA]   Station ${name#alert_}: no data written in last 30 mins" ;;
                alert_s*_size)       HEARTBEAT_BODY+=$'\n'"  [SIZE]      Station ${name#alert_}: no 15GB+ file in last 2 hours" ;;
                alert_*_24h)         ;;
                *)                   HEARTBEAT_BODY+=$'\n'"  [UNKNOWN]   $name" ;;
            esac
        done
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
            alert_chk*_escalate) _n="${name#alert_chk}"; echo "  [CHK 12h+]  CHK box ${_n%_escalate}: unreachable 12+ hours" ;;
            alert_chk*)          echo "  [CHK]       CHK box ${name#alert_chk}: unreachable" ;;
            alert_s*_wrlen)      echo "  [WRLEN]     Station ${name#alert_}: WR-LEN switch unreachable" ;;
            alert_s*_taxi)       echo "  [TAXI]      Station ${name#alert_}: TAXI DAQ unreachable" ;;
            alert_s*_live)       echo "  [NO DATA]   Station ${name#alert_}: no data written in last 30 mins" ;;
            alert_s*_size)       echo "  [SIZE]      Station ${name#alert_}: no 15GB+ file in last 2 hours" ;;
            alert_*_24h)         ;;  # internal marker, skip display
            *)                   echo "  [UNKNOWN]   $name" ;;
        esac
    done
elif [ $ERROR_FOUND -eq 0 ] && [ $RESOLVED_FOUND -eq 0 ]; then
    echo "System healthy."
fi
