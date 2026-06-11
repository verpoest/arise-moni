# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Automated monitoring system for the ARISE radio array (6 stations) at the Pierre Auger Observatory. Runs unattended on a remote laptop via cron. Processes binary DAQ event files, generates diagnostic plots, publishes static HTML reports, and sends email/Slack alerts on hardware or network failures.

## Running Scripts

All scripts source configuration from `config/common.env`. There is no build step, test suite, or linter.

```bash
# Process a specific day (defaults to yesterday)
python scripts/process_day.py --date 2026-02-12

# Analyze a single binary file with plots
python scripts/analyze_file.py /path/to/file.bin --plot --events 1000

# Run health check manually
bash scripts/monitor_health.sh

# Regenerate the static website from existing archive data
python scripts/update_web.py

# Pull CHK microcontroller sensor data
bash scripts/pull_chk_data.sh

# Install/update cron jobs (idempotent)
bash scripts/install_cron.sh
```

## Architecture

**Data flow:** Binary `.bin` files (written hourly per station by TAXI DAQ) → `analyze_file.py` (per-file analysis) → `process_day.py` (daily aggregation + plots) → `update_web.py` (static HTML site) → rsync/lftp to web server.

**Health monitoring** runs independently: `monitor_health.sh` checks disk, network (layered: WR-LEN → TAXI), data freshness, and CHK microcontroller reachability every 30 minutes. CHK checks are silent unless a box is unreachable for 12+ hours, at which point they escalate to a real alert. Uses sentinel files in `$LOG_DIR/alert_state/` for deduplication (one email per new problem, 24h follow-up, resolved notification). The daily heartbeat email lists any active issues.

### Key modules

- `scripts/utils.py` — Config loader (`load_config`) and binary file readers. Parses TAXI `.bin` files: 9-column uint16 rows, header marker `0x1000`, chunked memmap scanning with early exit.
- `scripts/analyze_file.py` — Single-file analysis: reads N events, computes median spectra (800 MHz sampling, 2048 bins → rfft), RMS, ROI, event rates from RTC timestamps (8.4211 ns/tick).
- `scripts/process_day.py` — Daily pipeline: globs all `.bin` files for a date, calls `analyze_single_file` per file, stacks arrays handling ragged shapes (truncates to min events), generates per-station and all-station plots, saves `.npz` + `.json` to `web/archive/YYYY-MM-DD/`.
- `scripts/update_web.py` — Rebuilds all `index.html` pages from archive data. Dark-themed single-page reports with date sidebar, health alert card (latest day only, reads `alert_history.csv`), and plot grids.
- `scripts/monitor_health.sh` — Bash health checker with layered network checks and sentinel-based alert state machine. Sends via `mutt` + optional Slack webhook.
- `scripts/pull_chk_data.sh` — Hourly SCP pull from CHK microcontrollers (skips current hour's file, deletes remote on success).

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

Copy `config/common.env.example` to `config/common.env` (gitignored). Key variables: `DATA_DIR`, `OUTPUT_DIR`, `LOG_DIR`, `WEB_DIR`, `EMAIL_RECIPIENTS`, per-station IPs (`WRLEN_IP_N`, `TAXI_IP_N`, `ARISE_CHK_STN_IP`).

## Dependencies

Python: `numpy`, `matplotlib`, `scipy`. System: `mutt` (email alerts), standard coreutils (`ping`, `find`, `df`, `ssh`, `scp`).
