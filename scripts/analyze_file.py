import os
import sys
import datetime
import numpy as np
import scipy.fft
import argparse
import matplotlib.pyplot as plt

try:
    import utils
except ImportError:
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    import utils


def parse_filename_info(filepath):
    """
    Extracts metadata from filenames like:
    s6_eventData_1770292829_2026-02-05_12-00-29.bin
    """
    fname = os.path.basename(filepath)
    parts = fname.replace('.bin', '').split('_')
    
    # Safety check on filename structure
    if len(parts) < 5:
        return None

    station = parts[0]  # s6
    # unix_time = parts[2] # 1770292829
    date_part = parts[3] # 2026-02-05
    time_part = parts[4] # 12-00-29
    
    # Construct a proper datetime object
    dt_str = f"{date_part} {time_part.replace('-', ':')}"
    try:
        dt_obj = datetime.datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None

    return {
        "station": station,
        "datetime": dt_obj,
        "filename": fname
    }


def calc_median_spectrum(waveforms):
    '''Calculate the median frequency spectrum over a number of waveforms.
    This assumes sampling rate of 800 MHz and 2048 bin waveforms.
    
    Parameters
    ----------
    waveforms : numpy.ndarray
        A 2D NumPy array of shape (N_events, 2048) containing waveforms.

    Returns
    -------
    freqs : numpy.ndarray
        Frequency values.
    median_spectrum: numpy.ndarray
        Median of DFT of all waveforms.
    '''
    spectra = scipy.fft.rfft(waveforms, axis=1)
    median_spectrum = np.median(np.abs(spectra), axis=0)
    freqs = scipy.fft.rfftfreq(2048, d=(1/800e6))
    return freqs, median_spectrum


def analyze_single_file(filepath, n_events=1000):
    """
    Main Logic: Reads header, extracts specific events, computes spectrum.
    """
    if not os.path.exists(filepath):
        print(f"File does not exist: {filepath}", file=sys.stderr)
        return None

    # 1. Parse Metadata
    meta = parse_filename_info(filepath)
    if not meta:
        print(f"Skipping malformed filename: {filepath}", file=sys.stderr)
        return None
    
    # 2. Get Event Offsets and Read Data
    offsets = utils.get_first_n_event_offsets(filepath, n_events=n_events + 1)
    event_data = utils.parse_specific_events_from_offsets(filepath, offsets)
    
    # 3. Get Moni Data
    waveform_data = event_data[0].reshape(event_data[0].shape[0], 3, 2, 4096)
    spectra = np.zeros((3,2,1025))
    for iant in range(3):
        for ich in range(2):
            wfs = waveform_data[:, iant, ich, :2048]
            freq, spec = calc_median_spectrum(wfs)
            spectra[iant, ich, :] = spec

    rms = waveform_data.std(axis=-1)
    rois = event_data[1]
    start_chs = event_data[2]

    return {
        "station": meta['station'],
        "timestamp": meta['datetime'].isoformat(),
        "filename": meta['filename'],
        "events_processed": len(event_data[0]),
        "spectra": spectra,
        "rms": rms,
        "roi": rois,
        "start_ch": start_chs
    }

# --- STANDALONE EXECUTION ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Analyze a single ARISE binary file.")
    parser.add_argument("file", help="Path to the .bin file")
    parser.add_argument("--events", type=int, default=1000, help="Number of events to read")
    parser.add_argument("--plot", action='store_true', help="Make plots for this file.")
    args = parser.parse_args()

    if not os.path.exists(args.file):
        print(f"Error: File not found: {args.file}")
        sys.exit(1)

    result = analyze_single_file(args.file, n_events=args.events)
    
    if not result:
        print("Analysis failed.")
        sys.exit(1)

    print(result)

    # plot spectra
    freq = scipy.fft.rfftfreq(2048, d=(1/800e6))
    spectra = result['spectra']
    plt.rcParams.update({'font.size': 12})
    colors = [plt.cm.tab20(i) for i in range(6)]

    plt.figure(figsize=(8, 5))
    for iant in range(3):
        for ich in range(2):
            plt.plot(freq[1:] / 1e6, spectra[iant][ich][1:], label=f'Ant {iant+1}, Ch {ich}', color=colors[iant*2 + ich], alpha=0.75)
    plt.yscale('log')
    plt.legend(ncol=3, loc='upper right')
    plt.title(f'{result['station']} - {result['timestamp']} - raw spectrum')
    plt.xlabel("Frequency (MHz)")
    plt.ylabel("Median Spectrum (a.u.)")
    plt.xlim(0, 400)

    filename = result["filename"].split('.')[0]
    plt.savefig(f"{filename}_spectrum_raw.png", bbox_inches='tight', dpi=350)

    # plot rms
    rms = result['rms']

    plt.figure(figsize=(10, 5))
    for iant in range(3):
        for ich in range(2):
            plt.plot(rms[:, iant, ich], '.', label=f'Ant {iant+1}, Ch {ich}', color=colors[iant*2 + ich], alpha=0.75)
    plt.legend(ncol=3, loc='upper right')
    plt.title(f'{result['station']} - {result['timestamp']} - raw RMS')
    plt.xlabel("$i_{event}$")
    plt.ylabel("RMS (ADC)")
    plt.xlim(0, len(rms))
    plt.ylim(0, 1.05 * rms.max())

    plt.savefig(f"{filename}_RMS_raw.png", bbox_inches='tight', dpi=350)
    
    # plot roi
    roi = result['roi']

    fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(10, 6), sharex=True, gridspec_kw={'hspace': 0.03})

    for iant in range(3):
        ax1.plot(roi[:, iant], '.', label=f'Ant {iant+1}', color=colors[iant*2], alpha=0.75)
        if iant > 0:
            ax2.plot(roi[:, iant] - roi[:, 0], '.', label=f'Ant {iant+1}'   , color=colors[iant*2], alpha=0.75)
            ax3.plot(roi[:, iant] - roi[:, 0], '.', label=f'Ant {iant+1}'   , color=colors[iant*2], alpha=0.75)

    ax1.set_xlim(0, len(roi))
    ax1.set_ylim(0, 1024)
    maxabsdiff = np.max(np.abs(roi[:] - roi[:, 0].reshape(1000,1)))
    ax2.set_ylim(-maxabsdiff, maxabsdiff)
    ax3.set_ylim(-5, 5)
    ax3.set_yticks(np.arange(-4, 5, 1))

    for ax in fig.get_axes():
        ax.grid(ls=':')

    ax1.set_ylabel('ROI')
    ax2.set_ylabel('$ROI - ROI_{Ant1}$')
    ax3.set_ylabel('$ROI - ROI_{Ant1}$')
    ax3.set_xlabel('$i_{event}$')
    ax1.set_title(f'{result['station']} - {result['timestamp']} - raw ROI')
    ax1.legend(ncol=3, framealpha=1, loc='upper right')
    fig.align_ylabels()

    plt.savefig(f"{filename}_ROI_raw.png", bbox_inches='tight', dpi=350)

    # plot start ch
    get_bin = np.vectorize(np.binary_repr)
    stch = result['start_ch']
    vals = np.unique(stch)
    counts = np.zeros((3, len(vals)))
    for iant in range(3):
        v, c = np.unique(stch[:, iant], return_counts=True)
        for i in range(len(v)):
            v_i = v[i]
            counts[iant][np.where(vals == v_i)[0]] += c[i] 

    plt.figure(figsize=(8,5))
    x_plot = np.arange(len(vals))
    width = 0.2
    for iant in range(3):
        plt.bar(x_plot + width*(iant-1), counts[iant], width=width, label=f'Ant {iant+1}'   , color=colors[iant*2], alpha=0.75)

    xticks = [val.zfill(8) for val in get_bin(vals)]
    plt.xticks(x_plot, xticks, rotation=-45, ha='left')
    plt.legend(loc='upper left')
    plt.xlabel("Start. ch. value")
    plt.ylabel("# events")
    plt.title(f'{result['station']} - {result['timestamp']} - raw Start. Ch.')

    plt.savefig(f"{filename}_startch_raw.png", bbox_inches='tight', dpi=350)
        