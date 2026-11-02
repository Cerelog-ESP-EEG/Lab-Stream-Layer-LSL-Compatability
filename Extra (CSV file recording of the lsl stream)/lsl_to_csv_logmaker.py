"""
Records the Cerelog LSL stream (published by cerelog_lsl.py) to local CSV files.
Works with any channel count (8ch, 16ch, ...) - columns are taken from the stream.

Usage:
    1. In one terminal:  python cerelog_lsl.py
    2. In another:       python Extra/lsl_to_csv_logmaker.py

Options:
    --name NAME        LSL stream name to resolve (default: Cerelog_EEG, falls back to any EEG stream)
    --out DIR          Output folder (default: this Extra folder)
    --duration SECS    Stop automatically after this many seconds (default: run until Ctrl+C)
    --rotate-mins MIN  Start a new CSV file every MIN minutes (default: one file)

Same layout as the board's SD-card logs, minus the Status column (not sent over LSL):
    Timestamp, CH1 ... CH8   (or ... CH16)
    Timestamp - milliseconds since the log file started
    CHn       - signed ADC counts, converted back from the stream's microvolts.
                Note: cerelog_lsl.py DC-blocks the signal before streaming, so the
                DC offset of the raw ADC values is not present here.
"""
import argparse
import csv
import os
import time
from datetime import datetime

from pylsl import StreamInlet, resolve_byprop, proc_clocksync, proc_dejitter

# --- Configuration (must match cerelog_lsl.py) ---
DEFAULT_STREAM_NAME = 'Cerelog_EEG'
HARDWARE_VREF = 4.50
HARDWARE_GAIN = 24
GUI_CORRECTION_FACTOR = 0.94 * 24.0 / 10 / 2
# Inverse of convert_to_microvolts() in cerelog_lsl.py: streamed value -> ADC counts
UV_PER_COUNT = (2 * HARDWARE_VREF / HARDWARE_GAIN) / (2**24) * 1000000 * GUI_CORRECTION_FACTOR
RESOLVE_TIMEOUT = 5.0     # seconds per resolve attempt
FLUSH_INTERVAL = 1.0      # seconds between disk flushes
STATUS_INTERVAL = 5.0     # seconds between console status lines
NO_DATA_WARNING = 3.0     # warn if nothing arrives for this long

DEFAULT_OUT_DIR = os.path.dirname(os.path.abspath(__file__))


def find_stream(name):
    """Block until the named LSL stream is found, falling back to any EEG-type stream
    (e.g. a 16ch board publishing under a different name). Returns StreamInfo."""
    print(f"Searching for LSL stream '{name}'... (start cerelog_lsl.py if it isn't running)")
    while True:
        results = resolve_byprop('name', name, timeout=RESOLVE_TIMEOUT)
        if results:
            return results[0]
        results = resolve_byprop('type', 'EEG', timeout=1.0)
        if results:
            print(f"  '{name}' not found, using EEG stream '{results[0].name()}' instead")
            return results[0]
        print("  Not found yet, retrying...")


def open_csv(out_dir, info):
    """Create a new timestamped CSV file and write the header. Returns (file, writer, path)."""
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    path = os.path.join(out_dir, f"{info.name()}_{stamp}.csv")
    f = open(path, 'w', newline='')
    writer = csv.writer(f)
    writer.writerow(['Timestamp'] + [f"CH{i + 1}" for i in range(info.channel_count())])
    print(f"Writing to: {path}")
    return f, writer, path


def main():
    parser = argparse.ArgumentParser(description="Log the Cerelog LSL stream to CSV.")
    parser.add_argument('--name', default=DEFAULT_STREAM_NAME)
    parser.add_argument('--out', default=DEFAULT_OUT_DIR)
    parser.add_argument('--duration', type=float, default=None)
    parser.add_argument('--rotate-mins', type=float, default=None)
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)

    info = find_stream(args.name)
    print(f"Found stream: {info.name()} | type={info.type()} | "
          f"{info.channel_count()} ch @ {info.nominal_srate():.0f} Hz | host={info.hostname()}")

    # clocksync maps remote timestamps onto this machine's clock; dejitter smooths them
    inlet = StreamInlet(info, max_buflen=60, processing_flags=proc_clocksync | proc_dejitter)

    f, writer, path = open_csv(args.out, info)
    log_t0 = None  # LSL timestamp of the first sample in the current file
    start = time.monotonic()
    file_start = start
    last_flush = start
    last_status = start
    last_data = start
    total_samples = 0
    window_samples = 0

    print("\n>>> RECORDING (Ctrl+C to stop) >>>")
    try:
        while True:
            now = time.monotonic()
            if args.duration is not None and now - start >= args.duration:
                print(f"\nReached duration of {args.duration:.0f}s.")
                break

            samples, timestamps = inlet.pull_chunk(timeout=0.2, max_samples=1024)
            if timestamps:
                for sample, ts in zip(samples, timestamps):
                    if log_t0 is None:
                        log_t0 = ts
                    writer.writerow([round((ts - log_t0) * 1000)] + [round(v / UV_PER_COUNT) for v in sample])
                total_samples += len(timestamps)
                window_samples += len(timestamps)
                last_data = now
            elif now - last_data > NO_DATA_WARNING:
                print("  Warning: no data received - is cerelog_lsl.py still streaming?")
                last_data = now

            if now - last_flush >= FLUSH_INTERVAL:
                f.flush()
                last_flush = now

            if now - last_status >= STATUS_INTERVAL:
                rate = window_samples / (now - last_status)
                print(f"  {total_samples} samples | {rate:.1f} Hz | {now - start:.0f}s elapsed")
                window_samples = 0
                last_status = now

            if args.rotate_mins is not None and now - file_start >= args.rotate_mins * 60:
                f.close()
                f, writer, path = open_csv(args.out, info)
                file_start = now
                log_t0 = None

    except KeyboardInterrupt:
        print("\nStopping recording...")
    finally:
        f.close()
        inlet.close_stream()
        print(f"Saved {total_samples} samples. Last file: {path}")


if __name__ == "__main__":
    main()
