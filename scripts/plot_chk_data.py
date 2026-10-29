"""Plot the last 7 days of CHK sensor data for all stations.

Reads the binary CHK sensor files written by the microcontrollers (56-byte
records: double timestamp, float current mA, float voltage V, BME680
temperature/humidity/pressure, int gas resistance, then extra temperature and
dust-sensor fields) for the 6 ARISE stations (in ARISE_CHK_DATA_DIR/ST{i}/) and
the IceCube station (flat in ICECUBE_CHK_DATA_DIR), bins the data into 30-minute
averages, and writes two PNGs under WEB_DIR, both covering the same window:

- voltage_current_7d.png: battery voltage above current, all stations overlaid,
  30-min means with a min-max band per bin. Embedded on the latest web report.
- environment_7d.png: BME680 temperature, humidity, pressure and gas resistance,
  all stations overlaid, 30-min means. Opened from the "Environment data"
  button on the web report.

A record is kept only when its timestamp falls inside the file's own hour
window (and the requested 7-day window) and the voltage is non-zero — the same
safety filter used by check_chk_voltage.py.

The plots are regenerated every run with whatever data exists; stations with no
data simply do not appear. If no station has any data, labelled empty plots are
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
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.ticker import MaxNLocator

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

# 56 bytes: timestamp, current mA, voltage V, temperature C, humidity %,
# pressure hPa, gas Ohm (int), T0-T3 C, dust LED off/on
RECORD = struct.Struct("<dfffffiffffff")
FNAME_PREFIX = "sensors_data_UTC_"
FNAME_SUFFIX = ".bin"

BIN_MINUTES = 30
OUTPUT_NAME = "voltage_current_7d.png"
ENV_OUTPUT_NAME = "environment_7d.png"

# Plotted quantities: record column, scale to plot unit, axis label, y-limits
# (None = auto), log scale. Current is stored in mA and plotted in A; gas
# resistance is stored in Ohm and plotted in kOhm on a log scale since it spans
# ~0.2 kOhm to ~1 MOhm. Voltage/current limits leave room for the min-max band.
QUANTITIES = {
    "voltage": dict(column=2, scale=1.0, label="Voltage (V)", ylim=(23, 30), log=False),
    "current": dict(column=1, scale=1e-3, label="Current (A)", ylim=(0, 3.6), log=False),
    "temperature": dict(column=3, scale=1.0, label="Temperature (°C)", ylim=None, log=False),
    "humidity": dict(column=4, scale=1.0, label="Humidity (%)", ylim=None, log=False),
    "pressure": dict(column=5, scale=1.0, label="Pressure (hPa)", ylim=None, log=False),
    "gas": dict(column=6, scale=1e-3, label="Gas res. (kΩ)", ylim=None, log=True),
}
POWER_QUANTITIES = ["voltage", "current"]
ENV_QUANTITIES = ["temperature", "humidity", "pressure", "gas"]


def read_station(data_dir, start, end):
    """Return (times, values) of 30-min bins in [start, end] for every quantity.

    values[q] = {'mean', 'min', 'max'} lists per bin, in plot units; times are
    the bin centres. Reads the CHK sensor files directly under data_dir.
    Records are kept only when their timestamp falls inside both the requested
    window and the file's own hour window, and the voltage is non-zero. All
    datetimes are timezone-aware UTC.
    """
    bins = collections.defaultdict(list)
    columns = [(QUANTITIES[q]["column"], QUANTITIES[q]["scale"]) for q in QUANTITIES]
    bin_seconds = BIN_MINUTES * 60
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
            record = RECORD.unpack_from(data, off)
            if record[2] == 0.0:
                continue
            try:
                dt = datetime.datetime.fromtimestamp(record[0], datetime.timezone.utc)
            except (OSError, ValueError, OverflowError):
                continue
            if start <= dt <= end and window_lo <= dt <= window_hi:
                # Bins are aligned to the Unix epoch, i.e. to UTC midnight for any width dividing a day
                bin_key = datetime.datetime.fromtimestamp(
                    record[0] // bin_seconds * bin_seconds, datetime.timezone.utc)
                bins[bin_key].append([record[c] * s for c, s in columns])
    keys = sorted(bins)
    offset = datetime.timedelta(minutes=BIN_MINUTES / 2)
    times = [t + offset for t in keys]
    values = {}
    for k, q in enumerate(QUANTITIES):
        samples = [[rec[k] for rec in bins[t]] for t in keys]
        values[q] = {
            "mean": [sum(x) / len(x) for x in samples],
            "min": [min(x) for x in samples],
            "max": [max(x) for x in samples],
        }
    return times, values


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


def split_runs(times, max_step):
    """Split sorted times into (lo, hi) index ranges wherever consecutive times are more than max_step apart."""
    runs = []
    lo = 0
    for k in range(1, len(times)):
        if times[k] - times[k - 1] > max_step:
            runs.append((lo, k))
            lo = k
    runs.append((lo, len(times)))
    return runs


def plot_series(ax, times, series, color, band, label=None):
    """Plot the mean line, plus a min-max band per bin if requested; both break at missing bins."""
    bin_width = datetime.timedelta(minutes=BIN_MINUTES)
    half_bin = bin_width / 2
    line_times, line_values = [], []
    for lo, hi in split_runs(times, bin_width):
        if band:
            # One step band per run, each bin shaded over its full width (bin start to bin end)
            edges = [t - half_bin for t in times[lo:hi]] + [times[hi - 1] + half_bin]
            lows = series["min"][lo:hi] + [series["min"][hi - 1]]
            highs = series["max"][lo:hi] + [series["max"][hi - 1]]
            ax.fill_between(edges, lows, highs, color=color, alpha=0.2, linewidth=0, step="post")
        if line_times:
            # NaN breaks the line across the gap
            line_times.append(times[lo])
            line_values.append(float("nan"))
        line_times.extend(times[lo:hi])
        line_values.extend(series["mean"][lo:hi])
    ax.plot(line_times, line_values, linewidth=1.0, color=color, label=label)


def make_plot(stations, all_data, start, end, quantities, title, output_name, band):
    """Overlay all stations, one panel per quantity, panels stacked with no gap."""
    n_q = len(quantities)
    fig, axes = plt.subplots(n_q, 1, figsize=(14, 4 + 2.5 * n_q), sharex=True,
                             gridspec_kw={"hspace": 0})
    fig.suptitle(title, fontsize=18, fontweight="bold")

    colors = plt.cm.tab10.colors
    plotted_any = False
    for i_q, (q, ax) in enumerate(zip(quantities, axes)):
        for i, (label, _) in enumerate(stations):
            times, values = all_data[label]
            if not times:
                continue
            plot_series(ax, times, values[q], colors[i % len(colors)], band,
                        label=label if i_q == 0 else None)
            plotted_any = True
        ax.set_ylabel(QUANTITIES[q]["label"], fontsize=13)
        if QUANTITIES[q]["ylim"]:
            ax.set_ylim(QUANTITIES[q]["ylim"])
        if QUANTITIES[q]["log"]:
            ax.set_yscale("log")
        else:
            # Prune the top tick of lower panels so labels don't collide at hspace=0
            ax.yaxis.set_major_locator(MaxNLocator(nbins="auto", steps=[1, 2, 5, 10],
                                                   prune="upper" if i_q > 0 else None))
        ax.grid(True, linestyle="--", linewidth=0.5, alpha=0.6)
        ax.tick_params(axis="both", labelsize=11)

    if "voltage" in quantities:
        # Threshold line at the low-voltage alert level used by check_chk_voltage.py.
        axes[quantities.index("voltage")].axhline(
            VOLTAGE_MIN, color="#f38ba8", linestyle="--", linewidth=1.2,
            label=f"Warning threshold ({VOLTAGE_MIN:.1f} V)")

    axes[-1].set_xlim(start, end)
    axes[-1].xaxis.set_major_locator(mdates.HourLocator(byhour=[0, 12]))
    axes[-1].xaxis.set_major_formatter(mdates.DateFormatter("%b %d\n%H:%M"))
    axes[-1].xaxis.set_minor_locator(mdates.HourLocator(byhour=[6, 18]))
    axes[-1].set_xlabel("Date (UTC)", fontsize=13)

    if not plotted_any:
        axes[0].text(0.5, 0.5, "No CHK data available", transform=axes[0].transAxes,
                     ha="center", va="center", fontsize=16, color="gray")

    # Key for what the line (and band) represent, in neutral gray
    key_handles = [Line2D([], [], color="0.35", linewidth=2.0)]
    key_labels = ["mean"]
    if band:
        key_handles.append(Patch(facecolor="0.35", alpha=0.25, linewidth=0))
        key_labels.append("min–max")
    key = axes[-1].legend(key_handles, key_labels, loc="lower right", ncol=len(key_handles),
                          fontsize=11, framealpha=0.85, handlelength=1.8, columnspacing=1.2)
    key.set_zorder(10)
    # Keep this legend when the station legend is added
    axes[-1].add_artist(key)

    handles, labels = [], []
    for ax in axes:
        h, l = ax.get_legend_handles_labels()
        handles += h
        labels += l
    plt.tight_layout(rect=[0, 0, 1, 0.97])
    # Below the x-axis label, the same distance for any number of panels
    if labels:
        legend = fig.legend(handles, labels, loc="upper center", bbox_to_anchor=(0.5, 0),
                            ncol=len(labels), fontsize=11)
        for line in legend.get_lines():
            line.set_linewidth(2.0)
    os.makedirs(WEB_DIR, exist_ok=True)
    output_file = os.path.join(WEB_DIR, output_name)
    plt.savefig(output_file, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved {output_file} ({sum(1 for l, _ in stations if all_data[l][0])} "
          f"of {len(stations)} stations have data)")


def main():
    end = datetime.datetime.now(datetime.timezone.utc)
    start = end - datetime.timedelta(days=7)
    stations = configured_stations()
    all_data = {label: read_station(d, start, end) for label, d in stations}
    make_plot(stations, all_data, start, end, POWER_QUANTITIES,
              f"Battery Voltage & Current — Last 7 Days (UTC), {BIN_MINUTES}-min averages, All Stations",
              OUTPUT_NAME, band=True)
    make_plot(stations, all_data, start, end, ENV_QUANTITIES,
              f"CHK Environment — Last 7 Days (UTC), {BIN_MINUTES}-min averages, All Stations",
              ENV_OUTPUT_NAME, band=False)


if __name__ == "__main__":
    main()
