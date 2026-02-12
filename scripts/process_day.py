import os
import sys
import glob
import json
import datetime
import argparse
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import scipy

# import modules from arise-moniWe 
try:
    import utils
    import analyze_file
except ImportError:
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    import utils
    import analyze_file

# --- CONFIGURATION ---
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONF = utils.load_config(os.path.join(ROOT_DIR, "config", "common.env"))
DATA_DIR = CONF.get("DATA_DIR", "./data")
WEB_DIR = CONF.get("WEB_DIR", os.path.join(ROOT_DIR, "web"))

# Define which keys from analyze_single_file are heavy Numpy arrays
# These will be stacked, passed to plotting, and saved to .npz
ARRAY_KEYS = ['spectra', 'rms', 'roi', 'start_ch']

def process_day(date_obj):
    date_str = date_obj.strftime("%Y-%m-%d")
    print(f"--- Processing Day: {date_str} ---")
    
    # 1. Setup Output
    daily_archive_dir = os.path.join(WEB_DIR, "archive", date_str)
    os.makedirs(daily_archive_dir, exist_ok=True)

    # 2. Find Files
    search_pattern = os.path.join(DATA_DIR, f"*{date_str}*.bin")
    files = sorted(glob.glob(search_pattern))
    
    if not files:
        print(f"No files found for {date_str}")
        return

    # Structure: raw_station_data['s1'] = [ {file1_result}, {file2_result} ... ]
    raw_station_data = {}

    # --- 3. ANALYSIS LOOP ---
    print(f"Found {len(files)} files. Starting analysis...")
    
    for filepath in files:
        # Run single file analysis looking at n_events
        result = analyze_file.analyze_single_file(filepath, n_events=1000)
        
        if result:
            st = result['station']
            if st not in raw_station_data:
                raw_station_data[st] = []
            
            # We don't need 'station' inside the dict anymore
            del result['station']
            raw_station_data[st].append(result)
            
            print(f"  Processed {os.path.basename(filepath)}")
        else:
            print(f"  Failed/Skipped {os.path.basename(filepath)}")

    # --- 4. DATA AGGREGATION ---
    # We will split data into:
    #   A. plotted_data: Stacked arrays for plotting (e.g., all spectra for s1)
    #   B. metadata_list: Lightweight info for JSON (filenames, timestamps)
    
    arrays_for_npz = {} # Format: {'s1_spectra': ..., 's1_rms': ...}
    metadata_for_json = {} # Format: {'s1': [ {file1_meta}, ... ]}
    
    # This dict is purely for plotting
    # Format: {'s1': {'spectra': (N,3,2,1025), 'rms': (N,1000,3,2), 'timestamps': [...]}}
    data_for_plotting = {}

    for station, file_list in raw_station_data.items():
        if not file_list: continue
        
        print(f"Aggregating data for station {station}...")
        
        station_plot_data = {
            'timestamps': [f['timestamp'] for f in file_list],
            'filenames': [f['filename'] for f in file_list]
        }
        
        metadata_for_json[station] = []

        # Process Arrays
        for key in ARRAY_KEYS:
            try:
                # 1. Stack the arrays from all files for this station
                # Example: spectra (files) -> (N_files, 3, 2, 1025)
                stacked = np.array([f[key] for f in file_list])
                
                # 2. Add to NPZ dict
                arrays_for_npz[f"{station}_{key}"] = stacked
                
                # 3. Add to Plotting dict
                station_plot_data[key] = stacked
                
            except Exception as e:
                print(f"  Error stacking {key} for {station}: {e}")

        # Process Metadata (remove arrays from the list)
        for f in file_list:
            # Create a clean copy for JSON without the heavy arrays
            clean_meta = {k: v for k, v in f.items() if k not in ARRAY_KEYS}
            metadata_for_json[station].append(clean_meta)

        data_for_plotting[station] = station_plot_data

    # --- 5. PLOTTING ---
    print("Generatng daily plots...")
    generate_daily_plots(data_for_plotting, daily_archive_dir)

    # --- 6. SAVING TO DISK ---
    
    # Save Arrays (.npz)
    npz_path = os.path.join(daily_archive_dir, "daily_arrays.npz")
    print(f"Saving binary arrays to {npz_path}...")
    np.savez_compressed(npz_path, **arrays_for_npz)

    # Save Metadata (.json)
    json_path = os.path.join(daily_archive_dir, "daily_stats.json")
    print(f"Saving metadata to {json_path}...")
    with open(json_path, 'w') as f:
        json.dump(metadata_for_json, f, indent=2)

    print("Daily processing complete.")


def generate_daily_plots(station_data, output_dir):
    """
    Generates summary plots for each station.
    
    Parameters
    ----------
    station_data : dict
        Key: Station Name (e.g., 's1')
        Value: Dict containing stacked arrays:
               - 'spectra': (N_files, 3, 2, 1025)
               - 'rms':     (N_files, N_events, 3, 2)
               - 'roi':     (N_files, N_events, 3) 
               - 'start_ch':(N_files, N_events, 3)
               - 'timestamps': List of strings
    output_dir : str
        Path where PNGs should be saved.
    """
    
    plt.rcParams.update({'font.size': 12})
    colors = [plt.cm.tab20(i) for i in range(6)]

    for station, data in station_data.items():
        print(f"  Plotting {station}...")
        
        # Unpack commonly used variables
        # N_files = data['spectra'].shape[0]
        timestamps = data['timestamps'] # List of ISO strings
        day = timestamps[1].split('T')[0]

        # Daily median spectrum (all antennas, all channels)
        median_spec = np.median(data['spectra'], axis=0)
        freq = scipy.fft.rfftfreq(2048, d=(1/800e6))

        i = 0
        plt.figure(figsize=(8, 5))
        for iant in range(3):
            for ich in range(2):
                plt.plot(freq[1:] / 1e6, median_spec[iant][ich][1:], label=f"Ant {iant+1}, Ch {ich}", color=colors[iant*2 + ich], alpha=0.75)
        plt.yscale('log')
        plt.legend(ncol=3, loc='upper right')
        plt.title(f"{station} - {day} - raw spectrum")
        plt.xlabel("Frequency (MHz)")
        plt.ylabel("Median Spectrum (a.u.)")
        plt.xlim(0, 400)
        plt.savefig(os.path.join(output_dir, f"{station}_daily_spectrum.png"), bbox_inches='tight', dpi=400)
        plt.close()
        
        # Daily Spectrogram (all stations, antenna 1, channels averaged)
        # spectra shape: (N_files, 3, 2, 1025) -> mean -> (N_files, 3, 1025)
        avg_daily_spec = np.mean(data['spectra'], axis=2)
        
        plt.figure(figsize=(10, 6))
        plt.imshow(avg_daily_spec.T, aspect='auto', origin='lower', 
                    cmap='viridis', norm=plt.LogNorm()) 
        plt.colorbar(label='Median Power')
        plt.title(f"{station} - Daily Spectrogram")
        plt.xlabel("File Index (Time)")
        plt.ylabel("Frequency Bin")
        plt.savefig(os.path.join(output_dir, f"{station}_daily_spectrogram.png"), bbox_inches='tight', dpi=400)
        plt.close()

        # RMS stability
        plot_daily_rms_violins(station, data['rms'], output_dir)


def plot_daily_rms_violins(station, rms_data, output_dir):
    """
    rms_data: (N_files, N_events, 3, 2)
    Plots grouped violin plots (3 antennas side-by-side) for each file.
    """
    # 1. Average over channels (last axis) -> Shape: (N_files, N_events, 3)
    rms_avg_ch = np.mean(rms_data, axis=-1)
    
    N_files, N_events, N_ants = rms_avg_ch.shape
    
    # 2. Setup Figure
    # 24 files fit nicely in 14-16 inches width
    fig, ax = plt.subplots(figsize=(15, 6))
    
    colors = ['tab:blue', 'tab:orange', 'tab:green']
    labels = ['Ant 1', 'Ant 2', 'Ant 3']
    
    # Offsets to group the violins side-by-side: -0.25, 0, +0.25
    offsets = [-0.25, 0, 0.25]
    
    # 3. Loop through antennas
    for i_ant in range(3):
        # Prepare list of data arrays for violinplot
        # dataset needs to be a list of size N_files, where each element is (N_events,)
        dataset = [rms_avg_ch[i, :, i_ant] for i in range(N_files)]
        
        # Calculate X-positions for this antenna
        positions = np.arange(N_files) + offsets[i_ant]
        
        # Draw Violins
        parts = ax.violinplot(
            dataset, 
            positions=positions,
            widths=0.2,          # Keep them thin enough to not overlap neighbors
            showmeans=False, 
            showmedians=True, 
            showextrema=False
        )
        
        # Style
        for pc in parts['bodies']:
            pc.set_facecolor(colors[i_ant])
            pc.set_edgecolor('black')
            pc.set_alpha(0.7)
            pc.set_linewidth(0.5)
            
        parts['cmedians'].set_color('white')
        parts['cmedians'].set_linewidth(1.2)

    # 4. Final Formatting
    ax.set_title(f"{station} - RMS Distribution by Antenna")
    ax.set_ylabel("RMS (ADC)")
    ax.set_xlabel("File Index")
    
    # Set X-ticks to integers (0, 1, 2...)
    ax.set_xticks(np.arange(N_files))
    ax.set_xticklabels(np.arange(N_files))
    
    ax.grid(True, axis='y', linestyle='--', alpha=0.5)

    # Manual Legend
    legend_patches = [mpatches.Patch(color=c, label=l) for c, l in zip(colors, labels)]
    ax.legend(handles=legend_patches, loc='upper right')

    plt.tight_layout()
    plt.savefig(os.path.join(output_dir, f"{station}_rms_violins.png"), dpi=350, bbox_inches='tight')
    plt.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="YYYY-MM-DD", default=None)
    args = parser.parse_args()
    
    if args.date:
        target = datetime.datetime.strptime(args.date, "%Y-%m-%d")
    else:
        target = datetime.datetime.now() - datetime.timedelta(days=1)
        
    process_day(target)