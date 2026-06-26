"""Plot the last 7 days of CHK battery voltage for all stations on one panel.

Reads the binary CHK sensor files written by the microcontrollers (16-byte
records: double timestamp, float current mA, float voltage V) for the 6 ARISE
stations (in ARISE_CHK_DATA_DIR/ST{i}/) and the IceCube station (flat in
ICECUBE_CHK_DATA_DIR), bins the voltages into 30-minute averages, and overlays
every station on a single combined plot saved as a PNG under WEB_DIR. The web
report (update_web.py) embeds that PNG on the latest report page only.

Most records in a file are garbage from the pre-allocated circular buffer, so a
record is kept only when its timestamp falls inside the file's own hour window
(and the requested 7-day window) and the voltage is non-zero — the same garbage
filter used by check_chk_voltage.py.

The plot is regenerated every run with whatever data exists; stations with no
data simply do not appear. If no station has any data, a labelled empty plot is
written so the web page never shows a broken image.
"""

import collections
import datetime
import glob
import os
import struct
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import utils

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONF = utils.load_config(os.path.join(ROOT_DIR, "config", "common.env"))


def cfg_str(key, default=""):
    """Read a string config value, stripping an inline '# comment' and quotes.

    load_config strips surrounding quotes but not inline comments, so a blank
    entry like `ICECUBE_CHK_IP=""  # ...` would otherwise read as a truthy
    comment string. Mirror cfg_float's tolerance for the gate/path values."""
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
WEB_DIR = cfg_str("WEB_DIR", os.path.join(ROOT_DIR, "web"))
VOLTAGE_MIN = cfg_float("CHK_VOLTAGE_MIN", 24.2)

RECORD = struct.Struct("dff")  # 16 bytes: timestamp (d), current mA (f), voltage V (f)
FNAME_PREFIX = "sensors_data_UTC_"
FNAME_SUFFIX = ".bin"

BIN_MINUTES = 30
DEFAULT_YLIM = (23, 29)
OUTPUT_NAME = "battery_voltage_7d.png"


def read_station(data_dir, start, end):
    """Return (times, voltages) of 30-min-averaged voltages in [start, end].

    Reads the CHK sensor files directly under data_dir. Records are kept only
    when their timestamp falls inside both the requested window and the file's
    own hour window, and the voltage is non-zero (rejects circular-buffer
    garbage). All datetimes are timezone-aware UTC.
    """
    bins = collections.defaultdict(list)
    pattern = os.path.join(data_dir, FNAME_PREFIX + "*" + FNAME_SUFFIX)
    for filepath in sorted(glob.glob(pattern)):
        stamp = os.path.basename(filepath)[len(FNAME_PREFIX):-len(FNAME_SUFFIX)]
        try:
            file_dt = datetime.datetime.strptime(stamp, "%Y-%m-%d_%H").replace(
                tzinfo=datetime.timezone.utc)
        except ValueError:
            continue
        # Skip files whose hour window cannot overlap the requested window.
        if file_dt < start - datetime.timedelta(hours=1) or file_dt > end + datetime.timedelta(hours=1):
            continue
        window_lo = file_dt - datetime.timedelta(hours=1)
        window_hi = file_dt + datetime.timedelta(hours=2)
        try:
            with open(filepath, "rb") as f:
                data = f.read()
        except OSError:
            continue
        for off in range(0, len(data) - RECORD.size + 1, RECORD.size):
            ts, current, voltage = RECORD.unpack_from(data, off)
            if voltage == 0.0:
                continue
            try:
                dt = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc)
            except (OSError, ValueError, OverflowError):
                continue
            if start <= dt <= end and window_lo <= dt <= window_hi:
                bin_minute = (dt.minute // BIN_MINUTES) * BIN_MINUTES
                bin_key = dt.replace(minute=bin_minute, second=0, microsecond=0)
                bins[bin_key].append(voltage)
    if not bins:
        return [], []
    times = sorted(bins)
    voltages = [sum(bins[t]) / len(bins[t]) for t in times]
    return times, voltages


def configured_stations():
    """(label, data_dir) for every configured CHK box, mirroring
    check_chk_voltage.py: ARISE stations in ST{i}/ subdirs of ARISE_CHK_DATA_DIR,
    the IceCube box flat in ICECUBE_CHK_DATA_DIR. Unconfigured boxes are skipped."""
    stations = []
    for i in range(1, 7):
        if cfg_str(f"ARISE_CHK_ST{i}_IP"):
            stations.append((f"ST{i}", os.path.join(CHK_DATA_DIR, f"ST{i}")))
    if cfg_str("ICECUBE_CHK_IP") and ICECUBE_CHK_DATA_DIR:
        stations.append(("IceCube", ICECUBE_CHK_DATA_DIR))
    return stations


def make_plot(start, end):
    stations = configured_stations()
    all_data = {label: read_station(d, start, end) for label, d in stations}

    fig, ax = plt.subplots(figsize=(14, 8))
    fig.suptitle("Battery Voltage — Last 7 Days (UTC), All Stations",
                 fontsize=18, fontweight="bold")

    colors = plt.cm.tab10.colors
    plotted_any = False
    for i, (label, _) in enumerate(stations):
        times, voltages = all_data[label]
        if times:
            ax.plot(times, voltages, linewidth=1.0, label=label,
                    color=colors[i % len(colors)])
            plotted_any = True

    # Threshold line at the low-voltage alert level used by check_chk_voltage.py.
    ax.axhline(VOLTAGE_MIN, color="#f38ba8", linestyle="--", linewidth=1.2,
               label=f"threshold ({VOLTAGE_MIN:.1f} V)")

    ax.set_ylabel("Voltage (V)", fontsize=13)
    ax.set_ylim(DEFAULT_YLIM)
    ax.set_xlim(start, end)
    ax.xaxis.set_major_locator(mdates.HourLocator(byhour=[0, 12]))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d\n%H:%M"))
    ax.xaxis.set_minor_locator(mdates.HourLocator(byhour=[6, 18]))
    ax.set_xlabel("Date (UTC)", fontsize=13)
    ax.grid(True, linestyle="--", linewidth=0.5, alpha=0.6)
    ax.tick_params(axis="both", labelsize=11)

    if not plotted_any:
        ax.text(0.5, 0.5, "No CHK voltage data available", transform=ax.transAxes,
                ha="center", va="center", fontsize=16, color="gray")

    ncol = max(len(stations) + 1, 1)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.15), ncol=ncol, fontsize=11)

    plt.tight_layout(rect=[0, 0, 1, 0.97])
    os.makedirs(WEB_DIR, exist_ok=True)
    output_file = os.path.join(WEB_DIR, OUTPUT_NAME)
    plt.savefig(output_file, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved {output_file} ({sum(1 for l, _ in stations if all_data[l][0])} "
          f"of {len(stations)} stations have data)")


def main():
    end = datetime.datetime.now(datetime.timezone.utc)
    start = end - datetime.timedelta(days=7)
    make_plot(start, end)


if __name__ == "__main__":
    main()
