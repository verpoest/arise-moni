import os
import glob
import json
import csv
import shutil
import datetime
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# --- CONFIG ---
try:
    import utils
    ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    CONF = utils.load_config(os.path.join(ROOT_DIR, "config", "common.env"))
    WEB_DIR = CONF.get("WEB_DIR", os.path.join(ROOT_DIR, "web"))
    LOG_DIR = CONF.get("LOG_DIR", "")
except ImportError:
    ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    WEB_DIR = os.path.join(ROOT_DIR, "web")
    LOG_DIR = ""

ARCHIVE_DIR = os.path.join(WEB_DIR, "archive")

# --- HTML STYLING ---
CSS = """
<style>
    :root { --bg: #1e1e2e; --sidebar: #181825; --text: #cdd6f4; --accent: #89b4fa; --card: #313244; }
    body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background: var(--bg); color: var(--text); display: flex; height: 100vh; overflow: hidden; }
    
    /* Sidebar */
    nav { width: 220px; background: var(--sidebar); border-right: 1px solid #45475a; display: flex; flex-direction: column; flex-shrink: 0; }
    nav .header { padding: 20px; font-weight: bold; border-bottom: 1px solid #45475a; color: var(--accent); font-size: 1.2em; }
    nav .scroll-area { flex: 1; overflow-y: auto; padding: 10px 0; }
    nav a { display: block; padding: 10px 20px; color: var(--text); text-decoration: none; font-size: 0.9em; border-left: 3px solid transparent; transition: background 0.2s; }
    nav a:hover { background: var(--card); }
    nav a.active { background: var(--card); border-left-color: var(--accent); color: var(--accent); font-weight: bold; }
    
    /* Main Content */
    main { flex: 1; overflow-y: auto; padding: 40px; }
    .container { max-width: 1400px; margin: 0 auto; }
    
    /* Headers */
    header { border-bottom: 2px solid var(--accent); margin-bottom: 30px; padding-bottom: 10px; }
    h1 { margin: 0; display: flex; justify-content: space-between; align-items: baseline; }
    h2 { color: var(--accent); margin-top: 0; font-size: 1.5rem; }
    .sub-text { font-size: 0.5em; font-weight: normal; color: #a6adc8; }
    
    /* Cards */
    .card { background: var(--card); border-radius: 8px; padding: 20px; margin-bottom: 40px; box-shadow: 0 4px 6px rgba(0,0,0,0.3); }
    
    /* Plot Grid - UPDATED FOR UNIFORM SIZING */
    .plot-grid { 
        display: grid; 
        grid-template-columns: repeat(auto-fit, minmax(400px, 1fr)); 
        gap: 20px; 
        margin-top: 15px; 
    }
    .plot-item { 
        background: #1e1e2e; 
        padding: 15px; 
        border-radius: 4px; 
        text-align: center; 
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
    }
    .plot-title { 
        margin-bottom: 10px; 
        font-weight: bold; 
        color: #bac2de; 
        font-size: 0.9em; 
        text-transform: uppercase; 
        letter-spacing: 0.05em; 
    }
    img { 
        width: 100%; 
        height: 350px;             /* Forces all image containers to be exactly this tall */
        object-fit: contain;       /* Scales the image so it fits within the box without stretching */
        border-radius: 4px; 
    }
    
    .meta-tag { font-size: 0.8rem; background: #45475a; padding: 2px 8px; border-radius: 10px; color: #fff; vertical-align: middle; margin-left: 10px; }

    #back-to-top { position: fixed; bottom: 30px; right: 30px; background: var(--accent); color: #1e1e2e; border: none; border-radius: 6px; padding: 10px 16px; font-size: 0.85em; font-weight: bold; cursor: pointer; opacity: 0; transition: opacity 0.3s; pointer-events: none; }
    #back-to-top.visible { opacity: 1; pointer-events: auto; }

    /* Sidebar buttons */
    .nav-buttons { display: flex; gap: 8px; padding: 12px 20px; border-bottom: 1px solid #45475a; }
    .nav-btn { flex: 1; padding: 8px 0; border: 1px solid #45475a; border-radius: 6px; background: var(--card); color: var(--text); font-size: 0.8em; cursor: pointer; text-align: center; transition: background 0.2s; }
    .nav-btn:hover { background: #45475a; }

    /* Modal overlay */
    .modal-overlay { display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.75); z-index: 1000; align-items: center; justify-content: center; }
    .modal-overlay.active { display: flex; }
    .modal-content { background: var(--card); border-radius: 10px; padding: 30px; max-width: 700px; max-height: 80vh; overflow-y: auto; position: relative; box-shadow: 0 8px 32px rgba(0,0,0,0.5); }
    .modal-content img { width: 100%; height: auto; object-fit: contain; border-radius: 4px; }
    .modal-content h3 { color: var(--accent); margin-top: 20px; margin-bottom: 8px; }
    .modal-content h3:first-child { margin-top: 0; }
    .modal-content p, .modal-content li { font-size: 0.9em; line-height: 1.5; color: #bac2de; }
    .modal-close { position: absolute; top: 12px; right: 16px; background: none; border: none; color: var(--text); font-size: 1.5em; cursor: pointer; }
    .modal-close:hover { color: var(--accent); }
</style>
<script>
    document.addEventListener("DOMContentLoaded", function() {
        var btn = document.getElementById("back-to-top");
        var main = document.querySelector("main");
        main.addEventListener("scroll", function() {
            btn.classList.toggle("visible", main.scrollTop > 300);
        });
        btn.addEventListener("click", function() {
            main.scrollTo({ top: 0, behavior: "smooth" });
        });

        document.querySelectorAll("[data-modal]").forEach(function(trigger) {
            trigger.addEventListener("click", function() {
                document.getElementById(trigger.dataset.modal).classList.add("active");
            });
        });
        document.querySelectorAll(".modal-overlay").forEach(function(overlay) {
            overlay.addEventListener("click", function(e) {
                if (e.target === overlay) overlay.classList.remove("active");
            });
            overlay.querySelector(".modal-close").addEventListener("click", function() {
                overlay.classList.remove("active");
            });
        });
    });
</script>
"""

def get_alert_counts_7d():
    """Returns {entity: {type: count}} for alerts in the past 7 days, or None if CSV missing."""
    if not LOG_DIR:
        return None
    csv_path = os.path.join(LOG_DIR, "alert_history.csv")
    if not os.path.exists(csv_path):
        return None
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=7)
    counts = {}
    try:
        with open(csv_path, newline="") as f:
            reader = csv.DictReader(f, fieldnames=["timestamp", "type", "entity"])
            for row in reader:
                if row["timestamp"] == "timestamp":
                    continue
                try:
                    ts = datetime.datetime.fromisoformat(row["timestamp"])
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=datetime.timezone.utc)
                except (KeyError, ValueError):
                    continue
                alert_type = row.get("type", "")
                entity = row.get("entity", "")
                if alert_type.startswith("resolved_"):
                    continue
                if ts >= cutoff:
                    if entity not in counts:
                        counts[entity] = {}
                    counts[entity][alert_type] = counts[entity].get(alert_type, 0) + 1
    except OSError:
        return None
    return counts


DISK_ENTITIES = ["DATA_DISK", "DATA_DISK_FULL", "OUTPUT_DISK", "OUTPUT_DISK_FULL"]

def _alert_cell(val):
    color = "#f38ba8" if val > 0 else "#a6e3a1"
    return f'<td style="color:{color};font-weight:bold;text-align:center">{val}</td>'

def _count_cell(val):
    color = "#f38ba8" if val > 0 else "#a6e3a1"
    return f'<td style="color:{color};font-weight:bold;text-align:right">{val}</td>'

def get_alert_card_html(counts):
    """Renders the health alerts card given {entity: {type: count}} dict."""
    if counts is None:
        return ""

    th_left = "text-align:left;padding:4px 16px 4px 0;border-bottom:1px solid #45475a"
    th_right = "text-align:right;padding:4px 0 4px 16px;border-bottom:1px solid #45475a"
    th_center = "text-align:center;padding:4px 12px;border-bottom:1px solid #45475a"

    disk_rows = ""
    for entity in DISK_ENTITIES:
        c = sum(counts.get(entity, {}).values())
        disk_rows += f'<tr><td>{entity}</td>{_count_cell(c)}</tr>'

    chk_rows = ""
    for i in range(1, 7):
        c = sum(counts.get(f"chk{i}", {}).values())
        chk_rows += f'<tr><td>CHK {i}</td>{_count_cell(c)}</tr>'

    station_rows = ""
    for i in range(1, 7):
        st = counts.get(f"s{i}", {})
        station_rows += f'<tr><td>s{i}</td>{_alert_cell(st.get("wrlen", 0))}{_alert_cell(st.get("taxi", 0))}{_alert_cell(st.get("live", 0) + st.get("size", 0))}</tr>'

    tbl = "border-collapse:collapse;font-size:0.9em"
    return f"""
    <div class="card" style="margin-bottom:30px">
        <h2 style="margin-bottom:12px">Health alerts &mdash; past 7 days</h2>
        <div style="display:flex;gap:32px;flex-wrap:wrap">
            <div>
                <table style="{tbl}">
                    <thead><tr><th style="{th_left}">Disk</th><th style="{th_right}">Count</th></tr></thead>
                    <tbody>{disk_rows}</tbody>
                </table>
            </div>
            <div>
                <table style="{tbl}">
                    <thead><tr><th style="{th_left}">CHK Box</th><th style="{th_right}">Count</th></tr></thead>
                    <tbody>{chk_rows}</tbody>
                </table>
            </div>
            <div>
                <table style="{tbl}">
                    <thead><tr>
                        <th style="{th_left}">Station</th>
                        <th style="{th_center}">WR-LEN</th>
                        <th style="{th_center}">TAXI</th>
                        <th style="{th_center}">Data</th>
                    </tr></thead>
                    <tbody>{station_rows}</tbody>
                </table>
            </div>
        </div>
    </div>
    """


def get_global_plots_html(date_dir_rel):
    """Finds all-station plots (like rates)."""
    search_path = os.path.join(WEB_DIR, "archive", date_dir_rel, "all_*.png")
    files = glob.glob(search_path)
    
    if not files:
        return ""

    html = '<div class="card"><h2>All stations</h2><div class="plot-grid">'
    for fpath in sorted(files):
        fname = os.path.basename(fpath)
        title = "Event Rates" if "rates" in fname else fname
        
        html += f"""
        <div class="plot-item">
            <div class="plot-title">{title}</div>
            <a href="{fname}" target="_blank"><img src="{fname}" loading="lazy"></a>
        </div>
        """
    html += '</div></div>'
    return html

def get_station_plots_html(station, date_dir_rel):
    """Finds plots specific to a station based on your naming convention."""
    plot_types = [
        ("daily_spectrum", "Median Spectrum"),
        ("daily_spectrogram", "Spectrogram"),
        ("rms_violins", "RMS Stability")
    ]
    
    html = '<div class="plot-grid">'
    found_any = False
    
    for suffix, title in plot_types:
        fname = f"{station}_{suffix}.png"
        full_path = os.path.join(WEB_DIR, "archive", date_dir_rel, fname)
        
        if os.path.exists(full_path):
            html += f"""
            <div class="plot-item">
                <div class="plot-title">{title}</div>
                <a href="{fname}" target="_blank"><img src="{fname}" loading="lazy"></a>
            </div>
            """
            found_any = True

    html += '</div>'
    return html if found_any else "<p>No plots generated.</p>"

def generate_sidebar(all_dates, current_date):
    """Generates the navigation sidebar."""
    html = """
    <nav>
        <div class="header"><a href="../../index.html" style="color:inherit;text-decoration:none">ARISE Monitoring</a></div>
        <div class="nav-buttons">
            <button class="nav-btn" data-modal="modal-map">&#x1F5FA; Map</button>
            <button class="nav-btn" data-modal="modal-info">&#x2139; Info</button>
        </div>
        <div class="scroll-area">
    """
    for d in all_dates:
        active_class = 'class="active"' if d == current_date else ''
        link = f"../{d}/index.html"
        html += f'<a href="{link}" {active_class}>{d}</a>\n'
    
    html += """
        </div>
    </nav>
    """
    return html

def update_website():
    print("--- Updating Website ---")

    # Copy array map to web directory if not already there
    map_src = os.path.join(ROOT_DIR, "config", "ARISE_map.png")
    map_dst = os.path.join(WEB_DIR, "ARISE_map.png")
    if os.path.exists(map_src) and not os.path.exists(map_dst):
        shutil.copy2(map_src, map_dst)

    alert_counts = get_alert_counts_7d()

    if not os.path.exists(ARCHIVE_DIR):
        print("No archive directory found.")
        return

    dates = [d for d in os.listdir(ARCHIVE_DIR) 
             if os.path.isdir(os.path.join(ARCHIVE_DIR, d)) and d.startswith("20")]
    dates.sort(reverse=True)
    
    if not dates:
        print("No data found in archive.")
        return

    for date in dates:
        date_dir_path = os.path.join(ARCHIVE_DIR, date)
        json_path = os.path.join(date_dir_path, "daily_stats.json")
        
        stats = {}
        if os.path.exists(json_path):
            try:
                with open(json_path, 'r') as f:
                    stats = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                print(f"  Warning: could not read {json_path}: {e}")
        
        content_html = f"""
        <header>
            <h1>Report: {date} <span class="sub-text">{len(stats)} stations active</span></h1>
            <p style="margin:5px 0 0;font-size:0.85em;color:#a6adc8">All times in UTC (local Argentina time: 21:00 previous day &ndash; 21:00 this day)</p>
        </header>
        """

        if date == dates[0]:
            content_html += get_alert_card_html(alert_counts)
        content_html += get_global_plots_html(date)

        stations = sorted(stats.keys(), key=lambda x: int(x[1:]) if x[1:].isdigit() else x)
        
        if not stations:
            content_html += "<p>No station data found for this day (at the time of processing).</p>"

        for st in stations:
            st_meta = stats[st]
            n_files = len(st_meta)
            
            content_html += f"""
            <div class="card">
                <h2>{st} <span class="meta-tag">{n_files} files</span></h2>
                {get_station_plots_html(st, date)}
            </div>
            """

        modals_html = """
            <div class="modal-overlay" id="modal-map">
                <div class="modal-content">
                    <button class="modal-close">&times;</button>
                    <h3>Array Map</h3>
                    <img src="../../ARISE_map.png" alt="ARISE array map">
                </div>
            </div>
            <div class="modal-overlay" id="modal-info">
                <div class="modal-content">
                    <button class="modal-close">&times;</button>

                    <h3>Health Alerts</h3>
                    <p>The health alerts card (shown on the latest report only) summarizes monitoring alerts fired in the past 7 days.</p>
                    <p>The left column shows <strong>disk</strong> alerts for the data and output SSDs, and <strong>CHK box</strong> reachability (the ARISE environmental-monitoring microcontrollers). The right column diagnoses station issues in layers:</p>
                    <ul>
                        <li><strong>WR-LEN</strong> &mdash; White Rabbit LEN switch reachability. If down, TAXI and data checks are skipped.</li>
                        <li><strong>TAXI</strong> &mdash; TAXI DAQ computer reachability (checked only when WR-LEN is OK).</li>
                        <li><strong>Data</strong> &mdash; Data recording issues (file freshness and size), checked only when both network layers are healthy.</li>
                    </ul>
                    <p>A count of 0 (green) means no issues were detected in that category.</p>

                    <h3>Page Layout</h3>
                    <p>Each daily report shows diagnostic plots for the ARISE radio array. The sidebar lists available dates; the main panel shows an all-station event rate overview followed by per-station diagnostic cards.</p>

                    <h3>Hourly Files</h3>
                    <p>The DAQ writes one binary file per station per hour. Filenames encode the station, unix timestamp, date, and time (UTC). For plotting, each file's timestamp is rounded to the nearest hour (0&ndash;23) and placed at that position on the x-axis. Missing hours appear as gaps.</p>

                    <h3>Plots</h3>
                    <ul>
                        <li><strong>Event Rates</strong> &mdash; Estimated trigger rate (Hz) per station at each hour. Expected to be near 100 Hz.</li>
                        <li><strong>Median Spectrum</strong> &mdash; Median frequency spectrum (0&ndash;400 MHz) across all events in all hourly files, shown per antenna and channel.</li>
                        <li><strong>Spectrogram</strong> &mdash; Frequency content throughout the day (antenna 1, both channels averaged).</li>
                        <li><strong>RMS Stability</strong> &mdash; Violin plots of waveform RMS (ADC counts) per antenna at each hour.</li>
                    </ul>
                </div>
            </div>
        """

        full_html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>ARISE - {date}</title>
            <meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
            <meta http-equiv="Pragma" content="no-cache">
            <meta http-equiv="Expires" content="0">
            {CSS}
        </head>
        <body>
            {generate_sidebar(dates, date)}
            <button id="back-to-top">&#8593; Top</button>
            <main>
                <div class="container">
                    {content_html}
                </div>
            </main>
            {modals_html}
        </body>
        </html>
        """
        
        with open(os.path.join(date_dir_path, "index.html"), "w") as f:
            f.write(full_html)

    latest_date = dates[0]
    redirect_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta http-equiv="refresh" content="0; url=archive/{latest_date}/index.html" />
    </head>
    <body>
        <p>Redirecting to latest report: <a href="archive/{latest_date}/index.html">{latest_date}</a></p>
    </body>
    </html>
    """
    
    with open(os.path.join(WEB_DIR, "index.html"), "w") as f:
        f.write(redirect_html)

    print(f"Website updated. Latest: {latest_date}")

if __name__ == "__main__":
    update_website()