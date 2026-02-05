#!/bin/bash

# Load Central Config
source "$(dirname "$0")/../config/common.env"

# Flags
ERROR_FOUND=0
ERROR_MSG="WARNING: Issues detected on ARISE DAQ ($(hostname)):"

# ================= 1. DISK CHECK =================
DISK_USAGE=$(df "$DATA_DIR" | awk 'NR==2 {print $5}' | sed 's/%//')

if [ "$DISK_USAGE" -gt "$DISK_THRESHOLD_PERCENT" ]; then
    ERROR_FOUND=1
    ERROR_MSG+=$'\n\n[DISK FULL] Drive is at '"$DISK_USAGE"'% capacity.'
fi

# ================= 2. STATION CHECKS =================
for i in {1..6}; do
    STATION="s$i"
    
    # 1. Check if files exist (Liveness & Size)
    LIVE_CHECK=$(find "$DATA_DIR" -name "${STATION}_eventData_*.bin" -mmin -30 -size +0c | head -n 1)
    SIZE_CHECK=$(find "$DATA_DIR" -name "${STATION}_eventData_*.bin" -mmin -120 -size +15G | head -n 1)

    STATION_ERROR=0
    STATION_MSG=""

    if [ -z "$LIVE_CHECK" ]; then
        STATION_ERROR=1
        STATION_MSG="No data written in last 30 mins."
    elif [ -z "$SIZE_CHECK" ]; then
        STATION_ERROR=1
        STATION_MSG="No 15GB+ file generated in last 2 hours."
    fi

    # 2. If Data Check Failed, Check Connectivity
    if [ $STATION_ERROR -eq 1 ]; then
        ERROR_FOUND=1
        
        # Get the IP variable dynamically (e.g., STATION_IP_1)
        IP_VAR="STATION_IP_$i"
        TARGET_IP="${!IP_VAR}"
        
        # Default status
        CONN_STATUS=" (IP not configured)"

        if [ -n "$TARGET_IP" ]; then
            # Try to ping (1 packet, wait max 5 seconds)
            if ping -c 1 -W 5 "$TARGET_IP" &> /dev/null; then
                CONN_STATUS=" [NETWORK OK]"
            else
                CONN_STATUS=" [NETWORK UNREACHABLE]"
            fi
        fi

        ERROR_MSG+=$'\n[FAIL] Station '"$STATION"': '"$STATION_MSG""$CONN_STATUS"
    fi
done

# ================= 3. NOTIFICATION (MUTT) =================
if [ $ERROR_FOUND -eq 1 ]; then
    # Convert "email1,email2" from config into "email1 email2" for mutt arguments
    RECIPIENT_LIST=$(echo "$EMAIL_RECIPIENTS" | tr ',' ' ')
    
    # Send via mutt
    # The '--' ensures that addresses starting with - aren't read as flags
    echo "$ERROR_MSG" | mutt -s "ARISE MONI ALERT" -- $RECIPIENT_LIST
    
    echo "Issues found. Alerts sent via mutt."
else
    echo "System Healthy."
fi