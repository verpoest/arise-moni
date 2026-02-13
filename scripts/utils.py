import os
import numpy as np

# --- CONFIGURATION ---
def load_config(config_path):
    config = {}
    if not os.path.exists(config_path):
        return config
    with open(config_path, 'r') as f:
        for line in f:
            if line.strip().startswith('#') or not line.strip(): continue
            if '=' in line:
                key, val = line.strip().split('=', 1)
                config[key] = val.strip('"\'')
    return config


# --- READERS ---
# these are very similar to the functions for dealing with large binary files which
# were developed in the arise-filter project

def get_first_n_event_offsets(taxi_bin_filename, n_events=1000, chunk_size=10_000_000):
    """
    Scans the file and returns the byte offsets of the first n_events.
    Stops reading as soon as it finds enough.

    (based on 'taxi_tools.parse_timestamps_triggers_and_create_index_memmap_chunked' from arise-filter)
    """
    if not os.path.isfile(taxi_bin_filename):
        return None

    # 1. Memmap (Instant, no data read yet)
    bin_data_flat = np.memmap(taxi_bin_filename, dtype=np.uint16, mode='r')
    if bin_data_flat.size < 9: return None

    # Reshape (Virtual view)
    bin_data = bin_data_flat[: 9 * (bin_data_flat.size // 9)].reshape(-1, 9)

    # 2. Fast Alignment (Only reads first 40k rows)
    subset_for_check = bin_data[:min(40000, bin_data.shape[0])]
    header_counts = np.sum(subset_for_check == 0x1000, axis=0)
    header_index = np.argmax(header_counts)

    # 3. Chunked Processing with EARLY EXIT
    total_rows = bin_data.shape[0]
    global_header_indices = []
    found_count = 0

    for start_idx in range(0, total_rows, chunk_size):
        if found_count >= n_events: break # <--- The Optimization

        end_idx = min(start_idx + chunk_size, total_rows)
        chunk = bin_data[start_idx:end_idx]
        
        # Check if we need to roll the chunk based on alignment
        if header_index != 0:
            chunk = np.roll(chunk, -header_index, axis=1)

        # Find headers
        header_mask = (chunk[:, 0] == 0x1000)
        local_header_indices = np.where(header_mask)[0]
        
        if local_header_indices.size > 0:
            global_indices = local_header_indices + start_idx
            global_header_indices.append(global_indices)
            found_count += len(global_indices)

    if not global_header_indices: return None

    flat_indices = np.concatenate(global_header_indices)
    if len(flat_indices) > n_events: flat_indices = flat_indices[:n_events]

    # Return Byte Offsets (Row * 9 words * 2 bytes/word)
    return flat_indices.astype(np.uint64) * 18

def process_single_event_slice(event_slice):
    """Processes a small array chunk corresponding to a single event.

    This function takes a pre-sliced 2D NumPy array containing all the data
    for one event and extracts the waveform, ROI, start channel, and trace
    length information.

    Parameters
    ----------
    event_slice : numpy.ndarray
        A 2D NumPy array of shape (N_rows, 9) containing the binary data for one event.

    Returns
    -------
    tuple
        A tuple containing the parsed data for this single event:
        - data (ndarray): Waveform data of shape (3, 8, 1024).
        - rois (ndarray): ROI values of shape (3,).
        - start_chs (ndarray): Starting channels of shape (3,).
        - trace_lengths (ndarray): A single integer trace length.
    """
    nAnt = 3 # 3 antennas per taxi
    nCh = 2 * 4 # 2 channel per antenna times 4 DRS4 buffers per antenna channel
    nBin = 1024 # 1024 bins per DRS4 buffer

    # Initialize output arrays for this single event with default values.
    data = -np.ones((nAnt, nCh, nBin), dtype=np.int16)
    rois = -np.ones(nAnt, dtype=np.int16)
    start_chs = -np.ones(nAnt, dtype=np.int16)
    trace_length = 0
    rtc_time = 0

    # Get the type identifier from the first column of every row.
    event_types = event_slice[:, 0]

    # Find and process header data row (0x1000)
    header_mask = (event_types == 0x1000)
    if np.any(header_mask):
        # Grab the first matching row (should only be one header per event)
        header_row = event_slice[header_mask][0]
        
        # Combine the 4 words (16-bit each) into a 64-bit integer
        # Words are at indices 4, 5, 6, 7
        # Shift: [48, 32, 16, 0]
        rtc_time = np.sum(header_row[4:8].astype(np.int64) << [48, 32, 16, 0])

    # Find and process waveform data rows (0x4xxx).
    sample_mask = (event_types >= 0x4000) & (event_types < 0x4C00)
    sample_rows = event_slice[sample_mask]
    if sample_rows.size > 0:
        sample_types = sample_rows[:, 0]
        drs4_ids = (sample_types & 0x0C00) >> 10
        bin_ids = (sample_types & 0x03FF)
        samples = sample_rows[:, 1:]
        # Place samples into the data array for this event.
        data[drs4_ids, :, bin_ids] = samples

    # Find and process the cascading info row (0xA000).
    casc_mask = (event_types == 0xA000)
    if np.any(casc_mask):
        casc_row = event_slice[casc_mask][0]
        rois[:] = casc_row[5:8]
        # start_chs[:] = [get_start_ch_from_binary(num) for num in casc_row[1:4]]
        start_chs[:] = casc_row[1:4]
        trace_length = casc_row[1]

    return data, rois, start_chs, trace_length, rtc_time

def parse_specific_events_from_offsets(taxi_bin_filename, header_offsets):
    """Parses events from a TAXI file given the offset of the event header in the file.
    """

    # Create lists to accumulate the data from each requested event.
    data_list, rois_list, start_chs_list, trace_lengths_list, rtc_list = [], [], [], [], []
    
    print(f"Reading {len(header_offsets) - 1} specific events...")
    with open(taxi_bin_filename, 'rb') as f:
        # Sorting indices improves disk read performance by reducing seeking.
        for i in range(len(header_offsets) - 1):

            # Use the index to find the start and end byte of the event data.
            start_byte = header_offsets[i]
            end_byte = header_offsets[i + 1]
            
            # Seek to the start and read only the bytes for this single event.
            f.seek(start_byte)
            event_bytes = f.read(end_byte - start_byte)
            
            # Convert this small chunk of bytes into a usable NumPy array.
            event_slice = np.frombuffer(event_bytes, dtype=np.uint16).reshape(-1, 9)
            
            # Process the small slice to extract its data.
            data, rois, start_chs, trace_length, rtc = process_single_event_slice(event_slice)
            
            # Append the results to our lists.
            data_list.append(data)
            rois_list.append(rois)
            start_chs_list.append(start_chs)
            trace_lengths_list.append(trace_length)
            rtc_list.append(rtc)
    
    if not data_list:
        print("No valid events were read.")
        return None

    # After the loop, stack the lists of individual results into final NumPy arrays.
    final_data = np.stack(data_list)
    final_rois = np.stack(rois_list)
    final_start_chs = np.stack(start_chs_list)
    final_trace_lengths = np.array(trace_lengths_list)
    final_rtcs = np.array(rtc_list)
    
    return final_data, final_rois, final_start_chs, final_trace_lengths, final_rtcs