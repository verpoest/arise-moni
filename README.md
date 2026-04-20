# ARISE DAQ Monitoring

Automated monitoring scripts for the ARISE radio array at the Pierre Auger Observatory. The system processes binary event files from 6 stations, generates daily analysis plots, publishes static HTML reports, and sends email alerts when issues are detected — all designed to run unattended on a remote laptop.

## What It Does

- **Health monitoring** (`scripts/monitor_health.sh`): Runs periodically to check disk usage on both the data and output drives, verify that each station is writing data (file freshness and size thresholds), and test network reachability if a station looks unhealthy. Uses sentinel files to deduplicate alerts — each new problem triggers one email, a follow-up is sent every 24 hours while the problem persists, and a resolved notification is sent when it clears. A daily heartbeat email is also sent once every 24 hours to confirm the monitoring system is running. Alerts go out via `mutt` and optionally Slack. Email send attempts (successes and failures) are logged to `mail_errors.log` in `LOG_DIR`.

- **Daily processing** (`scripts/process_day.py`): Reads all binary `.bin` event files for the previous day, computes median power spectra, RMS noise levels, and event rates per station, and saves compressed numpy arrays and JSON stats to the archive.

- **Plot generation** (`scripts/process_day.py`): After processing, automatically generates PNG plots per station (spectrum, spectrogram, RMS violin plots) and a combined event-rate plot across all stations.

- **Web report generation** (`scripts/update_web.py`): Builds a static HTML website from the archived data and plots, with a date navigation sidebar and a redirect from `index.html` to the latest report.

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
| `STATION_IP_1` … `STATION_IP_6` | IP addresses of the 6 DAQ stations |

### 3. Set up cron jobs

Run the install script (reads paths from `config/common.env` automatically):

```bash
bash scripts/install_cron.sh
```

This installs:
- A health check every half hour (at :15 and :45)
- Daily processing + website update at 1:00 AM

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
