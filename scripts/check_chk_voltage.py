#!/usr/bin/env python3
"""Check the latest CHK voltage for each station (the 6 ARISE stations and the
IceCube station) and email a warning when it drops below CHK_VOLTAGE_MIN, or
when no valid recent data is available.

Reads the binary CHK sensor files written by the microcontrollers (16-byte
records: double timestamp, float current mA, float voltage V). Most records in
a file are garbage from the pre-allocated circular buffer, so records are kept
only when their timestamp falls inside the file's own hour window and both
current and voltage are non-zero (see the CHK data format notes).

Alert deduplication mirrors monitor_health.sh: a sentinel file per station/type
means one email per new problem and a resolved email when it clears. State is
kept separate from the bash monitors under $LOG_DIR/voltage_alert_state/.
"""

import datetime
import glob
import json
import os
import socket
import statistics
import struct
import subprocess
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import utils

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONF = utils.load_config(os.path.join(ROOT_DIR, "config", "common.env"))


def cfg_str(key, default=""):
    """Read a string config value, stripping an inline '# comment' and quotes.

    load_config strips surrounding quotes but not inline comments, so a blank
    entry like `ICECUBE_CHK_IP=""  # ...` would otherwise read as a truthy
    comment string (and a commented path would keep a stray quote/comment).
    Mirror cfg_float's tolerance for the gate/path values."""
    raw = CONF.get(key)
    if raw is None:
        return default
    return raw.split("#", 1)[0].strip().strip("\"'").strip()


def cfg_float(key, default):
    """Read a numeric config value, tolerating an inline '# comment'."""
    raw = CONF.get(key)
    if raw is None:
        return default
    try:
        return float(raw.split("#", 1)[0].strip())
    except ValueError:
        return default


CHK_DATA_DIR = cfg_str("ARISE_CHK_DATA_DIR")
ICECUBE_CHK_DATA_DIR = cfg_str("ICECUBE_CHK_DATA_DIR")
LOG_DIR = cfg_str("LOG_DIR")
EMAIL_RECIPIENTS = cfg_str("EMAIL_RECIPIENTS")
SLACK_WEBHOOK_URL = CONF.get("SLACK_WEBHOOK_URL", "")
VOLTAGE_MIN = cfg_float("CHK_VOLTAGE_MIN", 24.2)
MAX_AGE_MIN = cfg_float("CHK_VOLTAGE_MAX_AGE_MIN", 180)
WINDOW_MIN = 30  # median voltage over the last 30 min of valid records

RECORD = struct.Struct("dff")  # 16 bytes: timestamp (d), current mA (f), voltage V (f)
FNAME_PREFIX = "sensors_data_UTC_"
FNAME_SUFFIX = ".bin"

HOST = socket.gethostname()


def parse_valid_records(path, file_dt):
    """Return [(datetime, voltage), ...] of valid records in one file.

    Valid = timestamp inside the file's own hour window and current/voltage
    both non-zero. The window rejects circular-buffer garbage whose timestamps
    belong to other hours (1970s, far-future, etc.).
    """
    window_lo = file_dt - datetime.timedelta(hours=1)
    window_hi = file_dt + datetime.timedelta(hours=2)
    valid = []
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return valid
    for off in range(0, len(data) - RECORD.size + 1, RECORD.size):
        ts, current, voltage = RECORD.unpack_from(data, off)
        if voltage == 0.0 or current == 0.0:
            continue
        try:
            dt = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc)
        except (OSError, ValueError, OverflowError):
            continue
        if window_lo <= dt <= window_hi:
            valid.append((dt, voltage))
    return valid


def station_reading(data_dir):
    """Return (median_voltage, None) or (None, problem_description) for a station,
    reading the CHK sensor files directly under data_dir."""
    pattern = os.path.join(data_dir, FNAME_PREFIX + "*" + FNAME_SUFFIX)
    files = sorted(glob.glob(pattern))
    if not files:
        return None, "no CHK data files found"

    # Walk the newest files backward until we find valid records (the very
    # latest file can still be all garbage).
    valid = []
    for path in reversed(files[-3:]):
        stamp = os.path.basename(path)[len(FNAME_PREFIX):-len(FNAME_SUFFIX)]
        try:
            file_dt = datetime.datetime.strptime(stamp, "%Y-%m-%d_%H").replace(
                tzinfo=datetime.timezone.utc)
        except ValueError:
            continue
        valid = parse_valid_records(path, file_dt)
        if valid:
            break

    if not valid:
        return None, "no valid recent records (file holds only garbage/empty data)"

    valid.sort()
    latest_dt = valid[-1][0]
    age_min = (datetime.datetime.now(datetime.timezone.utc) - latest_dt).total_seconds() / 60.0
    if age_min > MAX_AGE_MIN:
        return None, f"latest valid reading is {age_min:.0f} min old (stale; no fresh data)"

    window_lo = latest_dt - datetime.timedelta(minutes=WINDOW_MIN)
    recent = [v for dt, v in valid if dt >= window_lo]
    return statistics.median(recent), None


# --- Alert state (sentinels) --------------------------------------------------
ALERT_STATE_DIR = os.path.join(LOG_DIR, "voltage_alert_state")

ERRORS = []     # new problems -> one alert email
RESOLVED = []   # cleared problems -> one resolved email


def fire(name, msg):
    sentinel = os.path.join(ALERT_STATE_DIR, name)
    if not os.path.exists(sentinel):
        open(sentinel, "w").close()
        ERRORS.append(msg)


def clear(name, msg):
    sentinel = os.path.join(ALERT_STATE_DIR, name)
    if os.path.exists(sentinel):
        os.remove(sentinel)
        RESOLVED.append(msg)


# --- Notification -------------------------------------------------------------
def send_slack(msg):
    if not SLACK_WEBHOOK_URL:
        return
    payload = json.dumps({"message": msg}).encode("utf-8")
    req = urllib.request.Request(
        SLACK_WEBHOOK_URL, data=payload, headers={"Content-type": "application/json"}
    )
    try:
        urllib.request.urlopen(req, timeout=10).read()
    except Exception as exc:  # noqa: BLE001 - best-effort notification
        print(f"WARNING: Slack notification failed: {exc}", file=sys.stderr)


def send_email(subject, body):
    recipients = EMAIL_RECIPIENTS.replace(",", " ").split()
    log = os.path.join(LOG_DIR, "mail_errors.log")
    stamp = datetime.datetime.now().isoformat()
    try:
        proc = subprocess.run(
            ["mutt", "-s", subject, "--"] + recipients,
            input=body, text=True, capture_output=True,
        )
        if proc.returncode != 0:
            with open(log, "a") as f:
                f.write(f"[{stamp}] EMAIL FAILED (exit {proc.returncode}): {subject}\n")
                f.write(f"mutt output: {proc.stdout}{proc.stderr}\n")
            print(f"ERROR: Failed to send email '{subject}' (exit {proc.returncode}). "
                  f"See {log}", file=sys.stderr)
        else:
            with open(log, "a") as f:
                f.write(f"[{stamp}] Email sent: {subject} -> {' '.join(recipients)}\n")
    except FileNotFoundError:
        print("ERROR: mutt not found; cannot send email.", file=sys.stderr)
    send_slack(f"{subject} on {HOST}:\n{body}")


def main():
    if not EMAIL_RECIPIENTS:
        sys.exit("ERROR: EMAIL_RECIPIENTS is not set in config. Cannot send alerts.")
    if not CHK_DATA_DIR:
        sys.exit("ERROR: ARISE_CHK_DATA_DIR is not set in config.")
    if not LOG_DIR:
        sys.exit("ERROR: LOG_DIR is not set in config.")
    os.makedirs(ALERT_STATE_DIR, exist_ok=True)

    # (label, data_dir) for every configured CHK box. ARISE stations live in
    # ST{i}/ subdirs of ARISE_CHK_DATA_DIR; the IceCube box writes flat into
    # ICECUBE_CHK_DATA_DIR. Unconfigured boxes are skipped like the other monitors.
    stations = []
    for i in range(1, 7):
        if cfg_str(f"ARISE_CHK_ST{i}_IP"):
            stations.append((f"ST{i}", os.path.join(CHK_DATA_DIR, f"ST{i}")))
    if cfg_str("ICECUBE_CHK_IP") and ICECUBE_CHK_DATA_DIR:
        stations.append(("IceCube", ICECUBE_CHK_DATA_DIR))

    ongoing = []
    for station, data_dir in stations:
        median_v, problem = station_reading(data_dir)

        if problem is not None:
            fire(f"alert_{station}_voltage_data", f"[CHK DATA] {station}: {problem}.")
            ongoing.append(f"  [CHK DATA] {station}: {problem}")
            continue
        clear(f"alert_{station}_voltage_data", f"{station}: CHK data is available again.")

        if median_v < VOLTAGE_MIN:
            fire(f"alert_{station}_voltage_low",
                 f"[CHK VOLTAGE] {station}: voltage {median_v:.2f} V is below "
                 f"{VOLTAGE_MIN:.2f} V threshold.")
            ongoing.append(f"  [CHK VOLTAGE] {station}: {median_v:.2f} V (< {VOLTAGE_MIN:.2f} V)")
        else:
            clear(f"alert_{station}_voltage_low",
                  f"{station}: CHK voltage recovered ({median_v:.2f} V, "
                  f"above {VOLTAGE_MIN:.2f} V).")
            print(f"OK {station}: {median_v:.2f} V")

    now = datetime.datetime.now()
    if ERRORS:
        body = (f"WARNING: CHK voltage issues detected on ARISE DAQ ({HOST}):\n"
                f"Timestamp: {now}\n\n" + "\n".join(ERRORS))
        send_email("ARISE MONI CHK VOLTAGE ALERT", body)
        print("New CHK voltage issues found. Alert sent via mutt and Slack.")
    if RESOLVED:
        body = (f"CHK voltage issues resolved on ARISE DAQ ({HOST}):\n"
                f"Timestamp: {now}\n\n" + "\n".join("[RESOLVED] " + r for r in RESOLVED))
        send_email("ARISE MONI CHK VOLTAGE RESOLVED", body)
        print("CHK voltage issues resolved. Notification sent via mutt and Slack.")

    if ongoing:
        print("Ongoing CHK voltage issues (alert already sent, no repeat email):")
        print("\n".join(ongoing))
    elif not ERRORS and not RESOLVED:
        print("All CHK voltages healthy.")


if __name__ == "__main__":
    main()
