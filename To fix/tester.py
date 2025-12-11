import serial
import serial.tools.list_ports
import struct
import time
import numpy as np
from pylsl import StreamInfo, StreamOutlet, local_clock

# --- Configuration ---
INITIAL_BAUD_RATE = 9600
FINAL_BAUD_RATE = 115200
FIRMWARE_BAUD_RATE_INDEX = 0x04
SAMPLING_RATE_HZ = 250.0 
SAMPLE_PERIOD = 1.0 / SAMPLING_RATE_HZ

# --- PHYSICS CONSTANTS ---
# We return to the settings that worked in your Plotter Script
HARDWARE_VREF = 4.50
HARDWARE_GAIN = 24 

# --- GUI CORRECTION SCALAR ---
# 1. The GUI divides incoming LSL data by 24 (assuming it's raw). So we multiply by 24.
# 2. We keep the ~0.61 calibration we found earlier to match the amplitude.
# Combined Factor: 24 * 0.6133 = 14.719
GUI_CORRECTION_FACTOR = 24.0 * 0.6133

# --- Packet Constants ---
DATA_PACKET_START_MARKER = 0xABCD
DATA_PACKET_END_MARKER = 0xDCBA
DATA_PACKET_TOTAL_SIZE = 37
HANDSHAKE_START_MARKER_1 = 0xAA
HANDSHAKE_END_MARKER_1 = 0xCC
PACKET_IDX_LENGTH = 2
PACKET_IDX_CHECKSUM = 34

# --- ADS1299 Constants ---
ADS1299_NUM_CHANNELS = 8
ADS1299_NUM_STATUS_BYTES = 3
ADS1299_BYTES_PER_CHANNEL = 3

# --- Port Detection ---
BOARD_USB_IDS = [{'vid': 0x1A86, 'pid': 0x7523}]
BOARD_DESCRIPTIONS = ["USB-SERIAL CH340", "CH340"]

def convert_to_microvolts(raw_val):
    """
    1. Calculate uV using REAL hardware physics (Vref 4.5, Gain 24).
       This preserves the best float resolution for small signals.
    2. Apply the Correction Factor so the GUI displays the right amplitude.
    """
    # Standard ADS1299 conversion (Matches your Plotter)
    scale_factor = (2 * HARDWARE_VREF / HARDWARE_GAIN) / (2**24)
    outputV = raw_val * scale_factor * 1000000
    
    # Scale up for the GUI
    return outputV * GUI_CORRECTION_FACTOR

def find_and_open_board():
    print("Searching for Cerelog Board...")
    ports = serial.tools.list_ports.comports()
    candidate_ports = [p.device for p in ports if (p.vid and p.pid and {'vid': p.vid, 'pid': p.pid} in BOARD_USB_IDS) or (p.description and any(desc.lower() in p.description.lower() for desc in BOARD_DESCRIPTIONS))]
    if not candidate_ports: candidate_ports = [p.device for p in ports]

    for port_name in candidate_ports:
        print(f"--- Testing port: {port_name} ---")
        ser = None
        try:
            ser = serial.Serial(port_name, INITIAL_BAUD_RATE, timeout=2)
            time.sleep(5)
            if ser.in_waiting > 0: ser.read(ser.in_waiting)

            print(f"Sending handshake...")
            current_unix_time = int(time.time())
            checksum_payload = struct.pack('>BI', 0x02, current_unix_time) + bytes([0x01, FIRMWARE_BAUD_RATE_INDEX])
            checksum = sum(checksum_payload) & 0xFF
            handshake_packet = struct.pack('>BB', HANDSHAKE_START_MARKER_1, 0xBB) + checksum_payload + struct.pack('>B', checksum) + struct.pack('>BB', HANDSHAKE_END_MARKER_1, 0xDD)
            ser.write(handshake_packet)
            time.sleep(0.1) 
            ser.baudrate = FINAL_BAUD_RATE
            print(f"Switched to {ser.baudrate} baud...")
            time.sleep(0.5)
            ser.reset_input_buffer()

            bytes_received = ser.read(DATA_PACKET_TOTAL_SIZE * 5)
            if bytes_received and DATA_PACKET_START_MARKER.to_bytes(2, 'big') in bytes_received:
                print(f"SUCCESS on: {port_name}")
                return ser
            else:
                ser.close()
        except serial.SerialException:
            if ser: ser.close()
    return None

def main():
    print("Creating LSL Stream Outlet...")
    info = StreamInfo('Cerelog_EEG', 'EEG', ADS1299_NUM_CHANNELS, SAMPLING_RATE_HZ, 'float32', 'cerelog_uid_1234')
    outlet = StreamOutlet(info)

    ser = find_and_open_board()
    if not ser: return

    # --- DC BLOCKER VARIABLES ---
    # We use a filter: y[n] = x[n] - x[n-1] + R * y[n-1]
    # R = 0.995 is a standard coefficient for removing DC while keeping brainwaves.
    R = 0.995
    prev_x = [0.0] * ADS1299_NUM_CHANNELS
    prev_y = [0.0] * ADS1299_NUM_CHANNELS
    first_sample = True

    buffer = bytearray()
    start_marker = DATA_PACKET_START_MARKER.to_bytes(2, 'big')
    end_marker = DATA_PACKET_END_MARKER.to_bytes(2, 'big')
    
    # --- PRECISION TIMING ---
    next_schedule = local_clock()

    print("\n>>> STREAMING WITH ACTIVE DC BLOCKER >>>")
    print(f"Hardware Gain: {HARDWARE_GAIN} | GUI Correction: {GUI_CORRECTION_FACTOR:.2f}")
    
    try:
        while True:
            if ser.in_waiting > 0:
                buffer.extend(ser.read(ser.in_waiting))
            else:
                if len(buffer) < DATA_PACKET_TOTAL_SIZE:
                    time.sleep(0.001) 
                    continue

            while True:
                start_idx = buffer.find(start_marker)
                if start_idx == -1:
                    break
                if len(buffer) < start_idx + DATA_PACKET_TOTAL_SIZE:
                    break

                potential_packet = buffer[start_idx : start_idx + DATA_PACKET_TOTAL_SIZE]
                
                if potential_packet.endswith(end_marker):
                    payload = potential_packet[PACKET_IDX_LENGTH:PACKET_IDX_CHECKSUM]
                    if (sum(payload) & 0xFF) == potential_packet[PACKET_IDX_CHECKSUM]:
                        
                        # Anti-Jitter Wait
                        while local_clock() < next_schedule:
                            pass 

                        ads_data = potential_packet[7:34] 
                        lsl_sample = []
                        
                        for ch in range(ADS1299_NUM_CHANNELS):
                            idx = ADS1299_NUM_STATUS_BYTES + ch * ADS1299_BYTES_PER_CHANNEL
                            # Parse Raw Int
                            raw_val = int.from_bytes(ads_data[idx : idx + ADS1299_BYTES_PER_CHANNEL], byteorder='big', signed=True)
                            
                            # Convert to uV (High Precision, Scaled for GUI)
                            current_x = convert_to_microvolts(raw_val)
                            
                            # --- IIR DC BLOCKER FILTER ---
                            # This replaces the static offset. It tracks drift continuously.
                            if first_sample:
                                current_y = 0.0 # Start at zero
                            else:
                                # y[n] = x[n] - x[n-1] + R * y[n-1]
                                current_y = current_x - prev_x[ch] + (R * prev_y[ch])
                            
                            # Update history
                            prev_x[ch] = current_x
                            prev_y[ch] = current_y
                            
                            lsl_sample.append(current_y)
                        
                        first_sample = False
                        outlet.push_sample(lsl_sample, next_schedule)
                        next_schedule += SAMPLE_PERIOD
                        
                        buffer = buffer[start_idx + DATA_PACKET_TOTAL_SIZE:]
                        continue
                
                buffer = buffer[start_idx + 1:]

    except KeyboardInterrupt:
        print("\nStopping Stream...")
    finally:
        if ser: ser.close()

if __name__ == "__main__":
    main()