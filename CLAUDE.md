# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Automated monitoring system for the ARISE radio array (6 stations) at the Pierre Auger Observatory. Runs unattended on a remote laptop via cron. Processes binary DAQ event files, generates diagnostic plots, publishes static HTML reports, and sends email/Slack alerts on hardware or network failures.

## Running Scripts

All scripts source configuration from `config/common.env`. There is no build step, test suite, or linter.

```bash
# Process a specific day (defaults to yesterday)
python3 scripts/process_day.py --date 2026-02-12

# Analyze a single binary file with plots
python3 scripts/analyze_file.py /path/to/file.bin --plot --events 1000

# Run health check manually
bash scripts/monitor_health.sh

# Run the IceCube station health check manually
bash scripts/monitor_icecube.sh

# Regenerate the static website from existing archive data
python3 scripts/update_web.py

# Pull CHK microcontroller sensor data
bash scripts/pull_chk_data.sh

# Pull IceCube station CHK sensor data
bash scripts/pull_icecube_chk.sh

# Check latest CHK voltages against threshold (emails on low/missing data)
python3 scripts/check_chk_voltage.py

# Plot the last 7 days of CHK battery voltage for all stations (writes PNG to WEB_DIR)
python3 scripts/plot_chk_voltage.py

# Install/update cron jobs (idempotent)
bash scripts/install_cron.sh
```

## Architecture

**Data flow:** Binary `.bin` files (written hourly per station by TAXI DAQ) → `analyze_file.py` (per-file analysis) → `process_day.py` (daily aggregation + plots) → `update_web.py` (static HTML site) → rsync/lftp to web server.

**Health monitoring** runs independently: `monitor_health.sh` checks disk, network (layered: WR-LEN → TAXI), data freshness, and CHK microcontroller reachability every 30 minutes. CHK checks are silent unless a box is unreachable for 12+ hours, at which point they escalate to a real alert. Uses sentinel files in `$LOG_DIR/alert_state/` for deduplication (one email per new problem, 24h follow-up, resolved notification). The daily heartbeat email lists any active issues. The shared alert plumbing (sentinel state machine, `mutt`/Slack notification) lives in `scripts/alert_lib.sh`, sourced by the health monitors.

**IceCube station** is a 7th station that shares ARISE hardware (its own TAXI DAQ and ARISE CHK box) but is not part of ARISE — it writes data to a separate folder at a lower rate. It is monitored separately by `monitor_icecube.sh` (a reduced subset of the ARISE checks) and synced by `pull_icecube_chk.sh`, both fully isolated from ARISE: separate `$LOG_DIR/icecube_alert_state/` sentinels, separate `icecube_alert_history.csv`, `ICECUBE MONI …` email subjects, and no presence on the ARISE website.

### Key modules

- `scripts/utils.py` — Config loader (`load_config`) and binary file readers. Parses TAXI `.bin` files: 9-column uint16 rows, header marker `0x1000`, chunked memmap scanning with early exit.
- `scripts/analyze_file.py` — Single-file analysis: reads N events, computes median spectra (800 MHz sampling, 2048 bins → rfft), RMS, ROI, event rates from RTC timestamps (8.4211 ns/tick).
- `scripts/process_day.py` — Daily pipeline: globs all `.bin` files for a date, calls `analyze_single_file` per file, stacks arrays handling ragged shapes (truncates to min events), generates per-station and all-station plots, saves `.npz` + `.json` to `web/archive/YYYY-MM-DD/`.
- `scripts/update_web.py` — Rebuilds all `index.html` pages from archive data. Dark-themed single-page reports with date sidebar, health alert card and CHK battery-voltage card (both latest day only; the alert card reads `alert_history.csv`, the voltage card embeds `WEB_DIR/battery_voltage_7d.png`), and plot grids.
- `scripts/plot_chk_voltage.py` — Reads the same CHK sensor `.bin` files as `check_chk_voltage.py` (6 ARISE stations in `ARISE_CHK_DATA_DIR/ST{i}/`, IceCube flat in `ICECUBE_CHK_DATA_DIR`), averages voltage into 30-min bins over the last 7 days, and overlays all stations on one combined plot saved as `WEB_DIR/battery_voltage_7d.png` (with a dashed `CHK_VOLTAGE_MIN` threshold line). Runs in the daily cron chain between `process_day.py` and `update_web.py`. Reads string config via a local inline-comment-tolerant helper (`load_config` does not strip inline `#` comments).
- `scripts/monitor_health.sh` — Bash health checker for the 6 ARISE stations with layered network checks and sentinel-based alert state machine. Sources `alert_lib.sh`.
- `scripts/alert_lib.sh` — Shared alert plumbing sourced by the health monitors: `log_alert`, `fire_sentinel`/`clear_sentinel` (sentinel state machine), `send_slack`, `send_notification` (`mutt` + optional Slack). Callers set `ALERT_STATE_DIR`, `ALERT_HISTORY`, `RECIPIENT_LIST`, and the `*_FOUND`/`*_MSG` flag pairs.
- `scripts/monitor_icecube.sh` — Health checker for the IceCube station (WR-LEN → TAXI reachability, data freshness, CHK reachability with 12h escalation). Reuses `alert_lib.sh` but keeps its own isolated alert state/history.
- `scripts/pull_chk_data.sh` — Hourly SCP pull from the 6 ARISE CHK microcontrollers (skips current hour's file, deletes remote on success).
- `scripts/pull_icecube_chk.sh` — Single-box counterpart of `pull_chk_data.sh` for the IceCube CHK box, writing to its own data folder.
- `scripts/check_chk_voltage.py` — Reads the latest CHK sensor `.bin` files (16-byte `dff` records: timestamp, current mA, voltage V) for the 6 ARISE stations (in `ARISE_CHK_DATA_DIR/ST{i}/`) and the IceCube station (flat in `ICECUBE_CHK_DATA_DIR`), filters circular-buffer garbage (timestamp must fall in the file's hour window; current/voltage non-zero), and emails a warning when a station's median recent voltage drops below `CHK_VOLTAGE_MIN` or when no fresh valid data is available. Has its own Python sentinel dedup under `$LOG_DIR/voltage_alert_state/` (new-problem + resolved emails) and sends via `mutt` + optional Slack.

### Binary file format

TAXI `.bin` files contain uint16 values in 9-column rows. Row types identified by column 0:
- `0x1000`: Event header (RTC timestamp in columns 4–7 as 4×16-bit → 64-bit)
- `0x4000–0x4BFF`: Waveform samples (encodes DRS4 ID + bin index in type word)
- `0xA000`: Cascading info (ROI, start channels)

Each station has 3 antennas × 2 channels × 4 DRS4 buffers = 8 channels, 1024 bins each.

### Network topology

```
Server ──► WR-LEN switch (per station) ──► TAXI DAQ computer
                                         ──► ARISE CHK microcontroller
```

Health checks proceed layer by layer; deeper checks are skipped if an outer layer is unreachable.

## Configuration

Copy `config/common.env.example` to `config/common.env` (gitignored). Key variables: `DATA_DIR`, `OUTPUT_DIR`, `LOG_DIR`, `WEB_DIR`, `EMAIL_RECIPIENTS`, per-station IPs (`WRLEN_IP_N`, `TAXI_IP_N`, `ARISE_CHK_STN_IP`). IceCube station: `ICECUBE_WRLEN_IP`, `ICECUBE_TAXI_IP`, `ICECUBE_CHK_IP`, `ICECUBE_DATA_DIR`, `ICECUBE_DATA_PATTERN`, `ICECUBE_DATA_MAX_AGE_MIN`, `ICECUBE_CHK_DATA_DIR` (blank IPs skip the corresponding check). CHK voltage check: `CHK_VOLTAGE_MIN`, `CHK_VOLTAGE_MAX_AGE_MIN`.

## Dependencies

Python: `numpy`, `matplotlib`, `scipy`. System: `mutt` (email alerts), standard coreutils (`ping`, `find`, `df`, `ssh`, `scp`).
