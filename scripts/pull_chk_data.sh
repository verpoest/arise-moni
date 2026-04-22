#!/bin/bash
# Pull sensor data files from ARISE CHK microcontrollers.
# Runs hourly via cron. Skips the current UTC hour's file (still being
# written) and deletes successfully transferred files from the remote.

source "$(dirname "$0")/../config/common.env"

CHK_USER="debian"
CHK_REMOTE_DIR="/home/debian/arise/logdata"
SSH_TIMEOUT=10
CURRENT_HOUR_UTC=$(date -u +"%Y-%m-%d_%H")

if [ -z "$ARISE_CHK_DATA_DIR" ]; then
    echo "[$(date -Iseconds)] ERROR: ARISE_CHK_DATA_DIR is not set. Exiting."
    exit 1
fi

for i in {1..6}; do
    STATION_LABEL="ST${i}"
    IP_VAR="ARISE_CHK_ST${i}_IP"
    IP="${!IP_VAR}"

    if [ -z "$IP" ]; then
        echo "[$(date -Iseconds)] SKIP $STATION_LABEL: IP not configured ($IP_VAR)"
        continue
    fi

    LOCAL_DIR="${ARISE_CHK_DATA_DIR}/${STATION_LABEL}"
    mkdir -p "$LOCAL_DIR"

    if ! ssh -o ConnectTimeout=$SSH_TIMEOUT -o BatchMode=yes \
             "${CHK_USER}@${IP}" "echo ok" >/dev/null 2>&1; then
        echo "[$(date -Iseconds)] FAIL $STATION_LABEL ($IP): SSH unreachable"
        continue
    fi

    REMOTE_FILES=$(ssh -o ConnectTimeout=$SSH_TIMEOUT -o BatchMode=yes \
                       "${CHK_USER}@${IP}" "ls ${CHK_REMOTE_DIR}/sensors_data_UTC_*.bin 2>/dev/null")

    if [ -z "$REMOTE_FILES" ]; then
        echo "[$(date -Iseconds)] OK $STATION_LABEL ($IP): No files to pull"
        continue
    fi

    for REMOTE_FILE in $REMOTE_FILES; do
        FILENAME=$(basename "$REMOTE_FILE")

        if echo "$FILENAME" | grep -q "$CURRENT_HOUR_UTC"; then
            echo "[$(date -Iseconds)] SKIP $STATION_LABEL: $FILENAME (current hour, still writing)"
            continue
        fi

        if [ -f "${LOCAL_DIR}/${FILENAME}" ]; then
            echo "[$(date -Iseconds)] SKIP $STATION_LABEL: $FILENAME (already exists locally)"
            continue
        fi

        if scp -l 3000 -o ConnectTimeout=$SSH_TIMEOUT -o BatchMode=yes \
               "${CHK_USER}@${IP}:${REMOTE_FILE}" "${LOCAL_DIR}/${FILENAME}"; then

            if [ -s "${LOCAL_DIR}/${FILENAME}" ]; then
                ssh -o ConnectTimeout=$SSH_TIMEOUT -o BatchMode=yes \
                    "${CHK_USER}@${IP}" "rm -f '${REMOTE_FILE}'"
                echo "[$(date -Iseconds)] OK $STATION_LABEL: Pulled and deleted $FILENAME"
            else
                rm -f "${LOCAL_DIR}/${FILENAME}"
                echo "[$(date -Iseconds)] WARN $STATION_LABEL: $FILENAME transferred as 0 bytes, kept remote copy"
            fi
        else
            rm -f "${LOCAL_DIR}/${FILENAME}"
            echo "[$(date -Iseconds)] FAIL $STATION_LABEL: scp failed for $FILENAME"
        fi
    done
done

echo "[$(date -Iseconds)] CHK pull run complete."
