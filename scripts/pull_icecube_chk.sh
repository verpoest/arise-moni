#!/bin/bash
# Pull sensor data files from the IceCube station's ARISE CHK microcontroller.
# Runs hourly via cron. Skips the current UTC hour's file (still being written)
# and deletes successfully transferred files from the remote.
#
# Single-box counterpart to pull_chk_data.sh, kept separate so the IceCube CHK
# data lands in its own folder and can diverge (user/remote path) if needed.

source "$(dirname "$0")/../config/common.env"

CHK_USER="debian"
CHK_REMOTE_DIR="/home/debian/arise/logdata"
SSH_TIMEOUT=10
CURRENT_HOUR_UTC=$(date -u +"%Y-%m-%d_%H")

if [ -z "$ICECUBE_CHK_DATA_DIR" ]; then
    echo "[$(date -Iseconds)] ERROR: ICECUBE_CHK_DATA_DIR is not set. Exiting."
    exit 1
fi

if [ -z "$ICECUBE_CHK_IP" ]; then
    echo "[$(date -Iseconds)] SKIP IceCube: IP not configured (ICECUBE_CHK_IP)"
    exit 0
fi

IP="$ICECUBE_CHK_IP"
LOCAL_DIR="$ICECUBE_CHK_DATA_DIR"
mkdir -p "$LOCAL_DIR"

if ! ssh -o ConnectTimeout=$SSH_TIMEOUT -o BatchMode=yes \
         "${CHK_USER}@${IP}" "echo ok" >/dev/null 2>&1; then
    echo "[$(date -Iseconds)] FAIL IceCube ($IP): SSH unreachable"
    exit 0
fi

REMOTE_FILES=$(ssh -o ConnectTimeout=$SSH_TIMEOUT -o BatchMode=yes \
                   "${CHK_USER}@${IP}" "ls ${CHK_REMOTE_DIR}/sensors_data_UTC_*.bin 2>/dev/null")

if [ -z "$REMOTE_FILES" ]; then
    echo "[$(date -Iseconds)] OK IceCube ($IP): No files to pull"
    exit 0
fi

for REMOTE_FILE in $REMOTE_FILES; do
    FILENAME=$(basename "$REMOTE_FILE")

    if echo "$FILENAME" | grep -q "$CURRENT_HOUR_UTC"; then
        echo "[$(date -Iseconds)] SKIP IceCube: $FILENAME (current hour, still writing)"
        continue
    fi

    if [ -f "${LOCAL_DIR}/${FILENAME}" ]; then
        echo "[$(date -Iseconds)] SKIP IceCube: $FILENAME (already exists locally)"
        continue
    fi

    if scp -l 3000 -o ConnectTimeout=$SSH_TIMEOUT -o BatchMode=yes \
           "${CHK_USER}@${IP}:${REMOTE_FILE}" "${LOCAL_DIR}/${FILENAME}"; then

        if [ -s "${LOCAL_DIR}/${FILENAME}" ]; then
            ssh -o ConnectTimeout=$SSH_TIMEOUT -o BatchMode=yes \
                "${CHK_USER}@${IP}" "rm -f '${REMOTE_FILE}'"
            echo "[$(date -Iseconds)] OK IceCube: Pulled and deleted $FILENAME"
        else
            rm -f "${LOCAL_DIR}/${FILENAME}"
            echo "[$(date -Iseconds)] WARN IceCube: $FILENAME transferred as 0 bytes, kept remote copy"
        fi
    else
        rm -f "${LOCAL_DIR}/${FILENAME}"
        echo "[$(date -Iseconds)] FAIL IceCube: scp failed for $FILENAME"
    fi
done

echo "[$(date -Iseconds)] IceCube CHK pull run complete."
