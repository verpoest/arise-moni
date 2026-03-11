# ARISE DAQ Monitoring

Automated monitoring scripts for the ARISE radio array at the Pierre Auger Observatory. The system processes binary event files from 6 stations, generates daily analysis plots, publishes static HTML reports, and sends email alerts when issues are detected — all designed to run unattended on a remote laptop.

## What It Does

- **Health monitoring** (`scripts/monitor_health.sh`): Runs periodically to check disk usage, verify that each station is writing data (file freshness and size thresholds), and test network reachability if a station looks unhealthy. Sends an email alert via `mutt` if any issues are found.

- **Daily processing** (`scripts/process_day.py`): Reads all binary `.bin` event files for the previous day, computes median power spectra, RMS noise levels, and event rates per station, and saves compressed numpy arrays and JSON stats to the archive.

- **Plot generation** (`scripts/process_day.py`): After processing, automatically generates PNG plots per station (spectrum, spectrogram, RMS violin plots) and a combined event-rate plot across all stations.

- **Web report generation** (`scripts/update_web.py`): Builds a static HTML website from the archived data and plots, with a date navigation sidebar and a redirect from `index.html` to the latest report.

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
| `LOG_DIR` | Path where log files will be written |
| `WEB_DIR` | Output directory for the HTML reports |
| `EMAIL_RECIPIENTS` | Comma-separated list of alert email addresses |
| `DISK_THRESHOLD_PERCENT` | Disk usage percentage that triggers an alert |
| `STATION_IP_1` … `STATION_IP_6` | IP addresses of the 6 DAQ stations |

### 3. Set up cron jobs

Open the crontab editor:

```bash
crontab -e
```

Add the following lines (adjust paths to match your installation):

```cron
# Health check every hour
0 * * * * /bin/bash /path/to/arise-moni/scripts/monitor_health.sh >> /path/to/arise-moni/logs/health.log 2>&1

# Daily processing at 1:00 AM, followed by website update
0 1 * * * cd /path/to/arise-moni && python scripts/process_day.py >> logs/process.log 2>&1 && python scripts/update_web.py >> logs/web.log 2>&1
```

After saving, verify the cron jobs are registered:

```bash
crontab -l
```

The system will then run fully automatically: health checks every hour and a full processing + report cycle each night at 1 AM.
