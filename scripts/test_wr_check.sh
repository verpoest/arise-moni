#!/bin/bash
# Standalone test of ONLY the WR timestamp check from monitor_health.sh.
#
# Sends no mail, touches no sentinel state, writes no wrts_last marker: it picks
# the same candidate file the monitor would and prints what the monitor WOULD do.
# Safe to run as often as you like while the DAQ is running.

# ============================ SETTINGS ============================
DATA_DIR="/media/taxi/taxissd_3/data"
STATIONS="s1 s2 s3 s4 s5 s6"
MIN_FILE_SIZE="1G"        # candidate must be at least this big (as in the monitor)
MAX_AGE_MIN=240           # how far back to look for the last completed file
CHECKER="$(dirname "$0")/check_wr_timestamps.py"
# ==================================================================

if [ ! -f "$CHECKER" ]; then
    echo "ABORT: checker not found at $CHECKER" >&2
    echo "       (python3 would exit 2 here, which looks like a harmless"  >&2
    echo "        'cannot evaluate' result -- so this is checked up front.)" >&2
    exit 1
fi

for STATION in $STATIONS; do
    # 2nd-newest by mtime: the newest is the live file still being written
    FILE=$(timeout 30 find "$DATA_DIR" -name "${STATION}_eventData_*.bin" \
        -mmin -"$MAX_AGE_MIN" -size +"$MIN_FILE_SIZE" -printf '%T@ %p\n' 2>/dev/null \
        | sort -nr | sed -n '2p' | cut -d' ' -f2-)

    if [ -z "$FILE" ]; then
        printf '%-4s SKIP     no completed file >%s in the last %s min\n' \
            "$STATION" "$MIN_FILE_SIZE" "$MAX_AGE_MIN"
        continue
    fi

    OUT=$(timeout 60 python3 "$CHECKER" "$FILE" 2>&1); RC=$?
    case $RC in
        0)       ACTION="CLEAR    would clear the alert" ;;
        1)       ACTION="FIRE     WR timestamps wrong (unsynced, stalled or jumped)" ;;
        3)       ACTION="FIRE     no WR timestamps in the file" ;;
        2|124)   ACTION="neutral  inconclusive, sentinel untouched" ;;
        126|127) ACTION="FIRE     the checker itself could not be run" ;;
        *)       ACTION="?        unexpected exit code" ;;
    esac
    printf '%-4s %-8s rc=%-3s %s\n' "$STATION" "${ACTION%% *}" "$RC" "${ACTION#* }"
    printf '     file: %s\n     %s\n' "$(basename "$FILE")" "$OUT"
done
