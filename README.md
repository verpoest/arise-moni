# ARISE DAQ Monitoring

Automated monitoring scripts for the ARISE radio array at the Pierre Auger Observatory. The system processes binary event files from 6 stations, generates daily analysis plots, publishes static HTML reports, and sends email alerts when issues are detected — all designed to run unattended on a remote laptop.

## What It Does

- **Health monitoring** (`scripts/monitor_health.sh`): Runs periodically to check disk usage on both the data and output drives, check network reachability in layers (WR-LEN switch, then TAXI DAQ) for each station, and if both layers are healthy, verify that data is being written (file freshness and size thresholds). CHK microcontroller reachability is also tracked silently and escalates to an email alert if a box is unreachable for over 12 hours. Uses sentinel files to deduplicate alerts — each new problem triggers one email, a follow-up is sent every 24 hours while the problem persists, and a resolved notification is sent when it clears. A daily heartbeat email lists any active issues (or confirms all systems healthy). Alerts go out via `mutt` and optionally Slack. Email send attempts (successes and failures) are logged to `mail_errors.log` in `LOG_DIR`.

- **Daily processing** (`scripts/process_day.py`): Reads all binary `.bin` event files for the previous day, computes median power spectra, RMS noise levels, and event rates per station, and saves compressed numpy arrays and JSON stats to the archive.

- **Plot generation** (`scripts/process_day.py`): After processing, automatically generates PNG plots per station (spectrum, spectrogram, RMS violin plots) and a combined event-rate plot across all stations.

- **Web report generation** (`scripts/update_web.py`): Builds a static HTML website from the archived data and plots, with a date navigation sidebar and a redirect from `index.html` to the latest report.

- **CHK data pull** (`scripts/pull_chk_data.sh`): Pulls hourly sensor log files from the 6 ARISE CHK microcontrollers via SCP, storing them in station-specific subdirectories. Successfully transferred files are removed from the remote. The pulled data is not currently used by the rest of the monitoring pipeline.

- **IceCube station monitoring** (`scripts/monitor_icecube.sh`, `scripts/pull_icecube_chk.sh`): The IceCube station is a 7th station that shares ARISE hardware (its own TAXI DAQ and ARISE CHK box) but is not part of the ARISE array — it writes data to a separate folder at a lower rate. It is monitored by a dedicated script that runs a reduced subset of the health checks (WR-LEN → TAXI reachability, data freshness with a longer staleness threshold, and CHK reachability with the same 12-hour escalation), and its CHK sensor data is synced by a dedicated pull script. Both reuse the shared alert plumbing (`scripts/alert_lib.sh`) but stay fully isolated from ARISE: separate sentinel state, separate `icecube_alert_history.csv`, `ICECUBE MONI …` email subjects, and no presence on the ARISE website.

- **CHK voltage check** (`scripts/check_chk_voltage.py`): Reads the latest pulled CHK sensor data for each station and emails a warning if a station's voltage drops below `CHK_VOLTAGE_MIN` (default 24.2 V), or if no fresh valid data is available. The CHK files are pre-allocated circular buffers full of garbage records, so the script keeps only records whose timestamp falls within the file's own hour window and whose current/voltage are non-zero, then compares the median of the most recent valid readings against the threshold. It deduplicates alerts with sentinel files (one email per new low-voltage or missing-data event, a resolved email when it recovers) and sends via `mutt` and optionally Slack.

This project also **provides a simple script for manual checks** (`scripts/analyze_file.py`): Allows users to analyze a specific binary file on demand, generating plots and stats for that file.

## Setup

### 1. Install dependencies

```bash
pip install numpy matplotlib scipy
# Also requires mutt for email alerts
```

### 2. Configure

```bash
cp config/common.env.example config/common.env
```

Edit `config/common.env`:

| Variable | Description |
|---|---|
| `DATA_DIR` | Path to the directory containing station `.bin` files |
| `OUTPUT_DIR` | Path to the output/processed-data drive (monitored for I/O errors and capacity) |
| `LOG_DIR` | Path where log files and alert state will be written |
| `WEB_DIR` | Output directory for the HTML reports |
| `EMAIL_RECIPIENTS` | Comma-separated list of alert email addresses |
| `DISK_THRESHOLD_PERCENT` | Disk usage percentage that triggers an alert (applies to both drives) |
| `SLACK_WEBHOOK_URL` | Slack Workflow webhook URL for notifications (optional — leave empty to disable) |
| `WRLEN_IP_1` … `WRLEN_IP_6` | IP addresses of the 6 WR-LEN switches (one per station) |
| `TAXI_IP_1` … `TAXI_IP_6` | IP addresses of the 6 TAXI DAQ computers |
| `ARISE_CHK_ST1_IP` … `ARISE_CHK_ST6_IP` | IP addresses of the 6 ARISE CHK microcontrollers |
| `ARISE_CHK_DATA_DIR` | Local directory for storing pulled CHK sensor data |
| `ICECUBE_WRLEN_IP` | IceCube station WR-LEN switch IP (blank to skip this layer) |
| `ICECUBE_TAXI_IP` | IceCube station TAXI DAQ IP (blank to skip) |
| `ICECUBE_CHK_IP` | IceCube station ARISE CHK box IP (blank to skip) |
| `ICECUBE_DATA_DIR` | Local folder where IceCube data is written (blank to skip the freshness check) |
| `ICECUBE_DATA_PATTERN` | Filename glob for the IceCube freshness check (e.g. `*.bin`) |
| `ICECUBE_DATA_MAX_AGE_MIN` | Max age in minutes before a "no data" alert (tune to the lower data rate) |
| `ICECUBE_CHK_DATA_DIR` | Local directory for storing pulled IceCube CHK sensor data |
| `CHK_VOLTAGE_MIN` | Low-voltage warning threshold for the CHK boxes, in volts (default 24.2) |
| `CHK_VOLTAGE_MAX_AGE_MIN` | Max age (minutes) of the latest valid CHK reading before a stale/missing-data warning |

### 3. Set up cron jobs

Run the install script (reads paths from `config/common.env` automatically):

```bash
bash scripts/install_cron.sh
```

This installs:
- A health check every half hour (at :15 and :45)
- Daily processing + website update at 1:00 AM
- CHK data pull every hour (at :05)
- An IceCube station health check every half hour (at :25 and :55)
- IceCube CHK data pull every hour (at :10)
- A CHK voltage check every hour (at :20, after the CHK data pull)

Logs are written to `LOG_DIR` as set in `config/common.env`. The script is safe to re-run — existing arise-moni entries are replaced, not duplicated.

Verify with:

```bash
crontab -l
```

## Web Publishing

The generated website (`WEB_DIR`) can be published to a remote web server automatically. A typical setup uses an intermediate host that pulls from the processing machine via rsync and then pushes to the web server via lftp (useful when the web server only accepts sftp).

Ensure passwordless SSH is set up between each pair of machines, then add two cron jobs **on the intermediate host**:

**Pull from processing machine** — rsync to a local backup directory. Use `--bwlimit` (in KB/s) if the connection is bandwidth-constrained:
```
30 1 * * * rsync -av --delete --bwlimit=500 user@processing:/path/to/web/ /path/to/arise-moni-web/ >> /path/to/logs/sync.log 2>&1
```

**Push to web server** — lftp mirror (offset to run after rsync finishes):
```
0 2 * * * lftp sftp://webserver -e "mirror -R --delete /path/to/arise-moni-web/ /remote/web/path/; quit" >> /path/to/logs/deploy.log 2>&1
```

`mirror -R` only transfers new or changed files. Omit `--delete` to keep old archive entries on the server.
