# Recording the LSL Stream to CSV

To save the stream to local CSV files you must run **both** scripts at the same time, each in its own terminal window. Run both commands from the repo root (the `Lab-Stream-Layer-LSL-Compatability` folder).

**Terminal 1** – start the board stream (leave this running):

```
simon@Macmini Lab-Stream-Layer-LSL-Compatability % python3 cerelog_lsl.py
```

**Terminal 2** – once Terminal 1 shows `>>> STREAMING WITH ACTIVE DC BLOCKER >>>`, start the CSV logger:

```
simon@Macmini Lab-Stream-Layer-LSL-Compatability % python3 Extra/lsl_to_csv_logmaker.py
```

Press `Ctrl+C` in Terminal 2 to stop recording. The CSV is saved in this `Extra/` folder as `Cerelog_EEG_YYYYMMDD_HHMMSS.csv`.

Each row is `Timestamp, CH1 … CH8` (or `… CH16` on 16-channel boards), the same as the SD-card logs minus the Status column:
- **Timestamp** – milliseconds since the log started
- **CH1 … CHn** – signed ADC counts (DC offset removed, since `cerelog_lsl.py` DC-blocks the signal before streaming)

Optional flags: `--duration 60` (stop after 60 s), `--rotate-mins 10` (new file every 10 min), `--out DIR` (different folder), `--name NAME` (different stream name).

The OpenBCI GUI can stay connected while you record.
