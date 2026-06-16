#!/bin/bash

# Health monitor for the IceCube station.
#
# The IceCube station is separate from the 6 ARISE stations (its own TAXI DAQ
# and ARISE CHK box, its own data folder at a lower rate). This script runs a
# reduced set of the ARISE checks and keeps its alert state fully separate from
# ARISE, so nothing collides and nothing leaks into the ARISE web dashboard.
# It reuses the shared alert plumbing in alert_lib.sh.

# Load Central Config
source "$(dirname "$0")/../config/common.env"

# Separate state directory and history CSV (isolated from ARISE)
ALERT_STATE_DIR="$LOG_DIR/icecube_alert_state"
mkdir -p "$ALERT_STATE_DIR"
ALERT_HISTORY="$LOG_DIR/icecube_alert_history.csv"

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
ERROR_MSG="WARNING: Issues detected on IceCube DAQ ($(hostname)):
Timestamp: $(date)"
RESOLVED_FOUND=0
RESOLVED_MSG="Issues resolved on IceCube DAQ ($(hostname)):
Timestamp: $(date)"
FOLLOWUP_FOUND=0
FOLLOWUP_MSG="Issues still ongoing after 24h on IceCube DAQ ($(hostname)):
Timestamp: $(date)"

# ================= 1. NETWORK + DATA CHECKS (layered) =================
WRLEN_SENTINEL="$ALERT_STATE_DIR/alert_icecube_wrlen"
TAXI_SENTINEL="$ALERT_STATE_DIR/alert_icecube_taxi"
LIVE_SENTINEL="$ALERT_STATE_DIR/alert_icecube_live"

# --- Layer 1: WR-LEN switch ---
if [ -z "$ICECUBE_WRLEN_IP" ]; then
    WRLEN_OK=1
elif ping -c 1 -W 5 "$ICECUBE_WRLEN_IP" &> /dev/null; then
    clear_sentinel "$WRLEN_SENTINEL" wrlen icecube \
        "IceCube: WR-LEN switch ($ICECUBE_WRLEN_IP) is reachable again."
    WRLEN_OK=1
else
    fire_sentinel "$WRLEN_SENTINEL" wrlen icecube \
        "[FAIL] IceCube: WR-LEN switch ($ICECUBE_WRLEN_IP) is unreachable."
    WRLEN_OK=0
fi

# --- Layer 2: TAXI DAQ (only if WR-LEN is OK) ---
if [ $WRLEN_OK -eq 1 ]; then
    if [ -z "$ICECUBE_TAXI_IP" ]; then
        TAXI_OK=1
    elif ping -c 1 -W 5 "$ICECUBE_TAXI_IP" &> /dev/null; then
        clear_sentinel "$TAXI_SENTINEL" taxi icecube \
            "IceCube: TAXI DAQ ($ICECUBE_TAXI_IP) is reachable again."
        TAXI_OK=1
    else
        fire_sentinel "$TAXI_SENTINEL" taxi icecube \
            "[FAIL] IceCube: TAXI DAQ ($ICECUBE_TAXI_IP) is unreachable (WR-LEN OK)."
        TAXI_OK=0
    fi
else
    TAXI_OK=0
fi

# --- Layer 3: Data freshness (only if both network layers are OK) ---
# Lower data rate than ARISE, so the staleness threshold is configurable and
# the ARISE 15GB size check is intentionally omitted.
if [ $WRLEN_OK -eq 1 ] && [ $TAXI_OK -eq 1 ] && [ -n "$ICECUBE_DATA_DIR" ]; then
    LIVE_CHECK=$(timeout 30 find "$ICECUBE_DATA_DIR" -name "$ICECUBE_DATA_PATTERN" \
        -mmin -"$ICECUBE_DATA_MAX_AGE_MIN" -size +0c -print -quit 2>/dev/null)

    if [ -z "$LIVE_CHECK" ]; then
        fire_sentinel "$LIVE_SENTINEL" live icecube \
            "[FAIL] IceCube: No data written in last $ICECUBE_DATA_MAX_AGE_MIN mins (network OK)."
    else
        clear_sentinel "$LIVE_SENTINEL" live icecube \
            "IceCube: Data is being written again."
    fi
fi

# ================= 2. CHK BOX CHECK (silent, escalates at 12h) =================
CHK_SENTINEL="$ALERT_STATE_DIR/alert_icecube_chk"
CHK_ESC_SENTINEL="$ALERT_STATE_DIR/alert_icecube_chk_escalate"

if [ -z "$ICECUBE_CHK_IP" ]; then
    :
elif ping -c 1 -W 5 "$ICECUBE_CHK_IP" &> /dev/null; then
    clear_sentinel "$CHK_SENTINEL" chk icecube \
        "IceCube CHK box ($ICECUBE_CHK_IP) is reachable again." silent
    clear_sentinel "$CHK_ESC_SENTINEL" chk_escalate icecube \
        "IceCube CHK box ($ICECUBE_CHK_IP) is reachable again."
else
    fire_sentinel "$CHK_SENTINEL" chk icecube \
        "[FAIL] IceCube CHK box ($ICECUBE_CHK_IP) is unreachable." silent
    # Escalate to email alert if unreachable for over 12 hours
    if [ -n "$(find "$CHK_SENTINEL" -mmin +720 2>/dev/null)" ]; then
        fire_sentinel "$CHK_ESC_SENTINEL" chk_escalate icecube \
            "[FAIL] IceCube CHK box ($ICECUBE_CHK_IP) has been unreachable for over 12 hours."
    fi
fi

# ================= 3. NOTIFICATION =================
RECIPIENT_LIST=$(echo "$EMAIL_RECIPIENTS" | tr ',' ' ')

send_notification $ERROR_FOUND    "$ERROR_MSG"    "ICECUBE MONI ALERT"             "New issues found. Alerts sent via mutt and Slack."
send_notification $RESOLVED_FOUND "$RESOLVED_MSG" "ICECUBE MONI RESOLVED"          "Issues resolved. Notifications sent via mutt and Slack."
send_notification $FOLLOWUP_FOUND "$FOLLOWUP_MSG" "ICECUBE MONI ALERT (24h ongoing)" "24h follow-up sent via mutt and Slack."

# ================= 4. STATUS SUMMARY =================
ACTIVE_SENTINELS=( "$ALERT_STATE_DIR"/alert_* )

if [ -f "${ACTIVE_SENTINELS[0]}" ]; then
    echo "Ongoing known issues (alert already sent, no repeat email):"
    for sentinel in "${ACTIVE_SENTINELS[@]}"; do
        name=$(basename "$sentinel")
        case "$name" in
            alert_icecube_wrlen)         echo "  [WRLEN]     IceCube: WR-LEN switch unreachable" ;;
            alert_icecube_taxi)          echo "  [TAXI]      IceCube: TAXI DAQ unreachable" ;;
            alert_icecube_live)          echo "  [NO DATA]   IceCube: no fresh data written" ;;
            alert_icecube_chk_escalate)  echo "  [CHK 12h+]  IceCube CHK box: unreachable 12+ hours" ;;
            alert_icecube_chk)           echo "  [CHK]       IceCube CHK box: unreachable" ;;
            alert_*_24h)                 ;;  # internal marker, skip display
            *)                           echo "  [UNKNOWN]   $name" ;;
        esac
    done
elif [ $ERROR_FOUND -eq 0 ] && [ $RESOLVED_FOUND -eq 0 ]; then
    echo "IceCube station healthy."
fi
