#!/bin/bash

# Shared alert plumbing for arise-moni health monitors.
#
# This file only DEFINES functions; it sets no paths and prints nothing. Besides
# the alert functions it holds event_receiver_list, used by both monitors.
# A sourcing script must, before calling these functions, have set:
#   - ALERT_HISTORY        (CSV path for log_alert)
#   - RECIPIENT_LIST       (space-separated email list for send_notification)
#   - LOG_DIR              (for mail_errors.log)
#   - SLACK_WEBHOOK_URL    (optional, from common.env)
#   - the flag pairs ERROR_FOUND/ERROR_MSG, RESOLVED_FOUND/RESOLVED_MSG,
#     FOLLOWUP_FOUND/FOLLOWUP_MSG
# Bash resolves these globals at call time, so source order does not matter as
# long as they are set before the first call.

# Append one row to the alert history CSV when a new sentinel fires
log_alert() {
    local type="$1" entity="$2"
    if [ ! -f "$ALERT_HISTORY" ]; then
        echo "timestamp,type,entity" > "$ALERT_HISTORY"
    fi
    echo "$(date -Iseconds),$type,$entity" >> "$ALERT_HISTORY"
}

# fire_sentinel SENTINEL TYPE ENTITY "Message" [silent]
# - Creates sentinel and queues new alert if this is the first occurrence.
# - Checks for 24h follow-up if sentinel already exists.
# - If 5th arg is "silent", logs but does not queue email.
fire_sentinel() {
    local sentinel="$1" type="$2" entity="$3" msg="$4" silent="$5"
    if [ ! -f "$sentinel" ]; then
        touch "$sentinel"
        log_alert "$type" "$entity"
        if [ "$silent" != "silent" ]; then
            ERROR_FOUND=1
            ERROR_MSG+=$'\n\n'"$msg"
        fi
    fi
    local followup="${sentinel}_24h"
    if [ -n "$(find "$sentinel" -mmin +1440 2>/dev/null)" ]; then
        if [ ! -f "$followup" ] || [ -n "$(find "$followup" -mmin +1440 2>/dev/null)" ]; then
            touch "$followup"
            if [ "$silent" != "silent" ]; then
                FOLLOWUP_FOUND=1
                FOLLOWUP_MSG+=$'\n\n[STILL ONGOING 24h+] '"$msg"
            fi
        fi
    fi
}

# clear_sentinel SENTINEL TYPE ENTITY "Resolved description" [silent]
# - Removes sentinel and queues resolved notification if it was active.
# - If 5th arg is "silent", logs but does not queue email.
clear_sentinel() {
    local sentinel="$1" type="$2" entity="$3" msg="$4" silent="$5"
    if [ -f "$sentinel" ]; then
        rm -f "$sentinel" "${sentinel}_24h"
        log_alert "resolved_${type}" "$entity"
        if [ "$silent" != "silent" ]; then
            RESOLVED_FOUND=1
            RESOLVED_MSG+=$'\n\n[RESOLVED] '"$msg"
        fi
    else
        rm -f "${sentinel}_24h"
    fi
}

# event_receiver_list NAME
# One line per running receiver process: "<pid> <tag> <server-ip> <output-prefix>".
# The tag comes from the -p output prefix: ".../data/s5_" -> s5; a prefix ending
# in "/" (the IceCube receiver, own SSD, no station prefix) -> icecube; anything
# else -> unidentified. Shared so monitor_health.sh (ARISE stations) and
# monitor_icecube.sh (IceCube) agree on which process belongs to whom.
event_receiver_list() {
    pgrep -x -a "$1" 2>/dev/null | awk '
        NF {
            tag = "unidentified"; ip = "?"; pre = "?"
            for (i = 2; i <= NF; i++) {
                if ($i == "--server") ip = $(i+1)
                if ($i == "-p") {
                    pre = $(i+1); n = split(pre, parts, "/"); leaf = parts[n]
                    if (leaf ~ /^s[0-9]+_$/) { sub(/_$/, "", leaf); tag = leaf }
                    else if (leaf == "")      { tag = "icecube" }
                }
            }
            print $1, tag, ip, pre
        }'
}

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
