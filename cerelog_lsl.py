import serial
import serial.tools.list_ports
import struct
import time
import numpy as np
from pylsl import StreamInfo, StreamOutlet

# --- Configuration ---
INITIAL_BAUD_RATE = 9600
FINAL_BAUD_RATE = 115200
FIRMWARE_BAUD_RATE_INDEX = 0x04
SAMPLING_RATE_HZ = 250.0

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

def convert_to_microvolts(raw_val, vref=4.5, gain=24):
    """Converts raw 24-bit integer to microvolts."""
    scale_factor = (2 * vref / gain) / (2**24)
    return raw_val * scale_factor * 1_000_000

def find_and_open_board():
    """
    Scans ports, opens connection at 9600, sends handshake, 
    switches to 115200, and verifies stream.
    """
    print("Searching for Cerelog Board...")
    ports = serial.tools.list_ports.comports()
    
    # Filter ports based on VID/PID or Description
    candidate_ports = [
        p.device for p in ports 
        if (p.vid and p.pid and {'vid': p.vid, 'pid': p.pid} in BOARD_USB_IDS) or 
           (p.description and any(desc.lower() in p.description.lower() for desc in BOARD_DESCRIPTIONS))
    ]
    
    if not candidate_ports:
        print("No specific candidates found. Trying all ports...")
        candidate_ports = [p.device for p in ports]

    for port_name in candidate_ports:
        print(f"--- Testing port: {port_name} ---")
        ser = None
        try:
            # 1. Open at initial baud rate
            ser = serial.Serial(port_name, INITIAL_BAUD_RATE, timeout=2)
            print("Port opened. Waiting 5 seconds for board reset...")
            time.sleep(5)
            
            if ser.in_waiting > 0:
                ser.read(ser.in_waiting)

            # 2. Construct and Send Handshake
            print(f"Sending handshake to switch to {FINAL_BAUD_RATE} bps...")
            current_unix_time = int(time.time())
            # Handshake Payload: MsgType(0x02) + Timestamp(4 bytes) + ConfigReg(0x01) + BaudIndex
            checksum_payload = struct.pack('>BI', 0x02, current_unix_time) + bytes([0x01, FIRMWARE_BAUD_RATE_INDEX])
            checksum = sum(checksum_payload) & 0xFF
            
            # Full Packet: Start + Payload + Checksum + End
            handshake_packet = (
                struct.pack('>BB', HANDSHAKE_START_MARKER_1, 0xBB) + 
                checksum_payload + 
                struct.pack('>B', checksum) + 
                struct.pack('>BB', HANDSHAKE_END_MARKER_1, 0xDD)
            )
            
            ser.write(handshake_packet)
            time.sleep(0.1) # Brief pause before switching

            # 3. Switch Baud Rate
            ser.baudrate = FINAL_BAUD_RATE
            print(f"Switched to {ser.baudrate} baud. Verifying stream...")
            time.sleep(0.5)
            ser.reset_input_buffer()

            # 4. Verify Data Stream
            # Read enough bytes to find at least one packet start marker
            bytes_received = ser.read(DATA_PACKET_TOTAL_SIZE * 5)
            if bytes_received and DATA_PACKET_START_MARKER.to_bytes(2, 'big') in bytes_received:
                print(f"SUCCESS! Board found and streaming on: {port_name}")
                return ser
            else:
                print("No valid data stream detected.")
                ser.close()

        except serial.SerialException as e:
            print(f"Failed on {port_name}: {e}")
            if ser and ser.is_open:
                ser.close()
    
    return None

def main():
    # 1. Initialize LSL Info
    # Name: Cerelog_EEG
    # Type: EEG (Required by OpenBCI GUI)
    # Channels: 8
    # Rate: 250
    # Format: float32
    print("Creating LSL Stream Outlet...")
    info = StreamInfo('Cerelog_EEG', 'EEG', ADS1299_NUM_CHANNELS, SAMPLING_RATE_HZ, 'float32', 'cerelog_uid_1234')
    outlet = StreamOutlet(info)

    # 2. Connect to Board
    ser = find_and_open_board()
    if not ser:
        print("ERROR: Could not find board. Exiting.")
        return

    # 3. Stream Loop
    buffer = bytearray()
    start_marker = DATA_PACKET_START_MARKER.to_bytes(2, 'big')
    end_marker = DATA_PACKET_END_MARKER.to_bytes(2, 'big')
    
    print("\n>>> STREAMING DATA TO LSL >>>")
    print("Open your OpenBCI GUI now.")
    
    try:
        while True:
            # Read available bytes
            if ser.in_waiting > 0:
                buffer.extend(ser.read(ser.in_waiting))
            else:
                # Sleep briefly to be CPU friendly if no data
                time.sleep(0.001)
                continue

            # Parse Buffer
            while True:
                # Look for Start Marker
                start_idx = buffer.find(start_marker)
                if start_idx == -1:
                    # Keep the last byte just in case it's the first half of a marker
                    if len(buffer) > 0:
                        buffer = buffer[-1:]
                    break

                # Check if we have enough bytes for a full packet
                if len(buffer) < start_idx + DATA_PACKET_TOTAL_SIZE:
                    break

                # Extract potential packet
                potential_packet = buffer[start_idx : start_idx + DATA_PACKET_TOTAL_SIZE]
                
                # Verify End Marker
                if potential_packet.endswith(end_marker):
                    # Verify Checksum
                    payload = potential_packet[PACKET_IDX_LENGTH:PACKET_IDX_CHECKSUM]
                    calculated_checksum = sum(payload) & 0xFF
                    packet_checksum = potential_packet[PACKET_IDX_CHECKSUM]

                    if calculated_checksum == packet_checksum:
                        # --- Valid Packet Found ---
                        
                        # Extract Data Section (skip header/timestamp)
                        ads_data = potential_packet[7:34] # 27 bytes total
                        
                        lsl_sample = []
                        
                        # Parse 8 Channels
                        for ch in range(ADS1299_NUM_CHANNELS):
                            # Skip 3 status bytes + channel offset
                            idx = ADS1299_NUM_STATUS_BYTES + ch * ADS1299_BYTES_PER_CHANNEL
                            
                            raw_bytes = ads_data[idx : idx + ADS1299_BYTES_PER_CHANNEL]
                            
                            # Convert 24-bit big-endian to int
                            value = int.from_bytes(raw_bytes, byteorder='big', signed=True)
                            
                            # Convert to Microvolts
                            microvolts = convert_to_microvolts(value)
                            lsl_sample.append(microvolts)
                        
                        # Push to LSL
                        outlet.push_sample(lsl_sample)

                        # Remove processed packet from buffer
                        buffer = buffer[start_idx + DATA_PACKET_TOTAL_SIZE:]
                        continue
                    else:
                        print("Checksum mismatch")
                
                # If marker found but packet invalid, move forward 1 byte
                buffer = buffer[start_idx + 1:]

    except KeyboardInterrupt:
        print("\nStopping Stream...")
    except Exception as e:
        print(f"Error in stream loop: {e}")
    finally:
        if ser and ser.is_open:
            ser.close()
        print("Serial closed.")

if __name__ == "__main__":
    main()
