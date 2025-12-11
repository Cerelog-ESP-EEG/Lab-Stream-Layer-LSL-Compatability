import serial
import serial.tools.list_ports
import struct
import time
import os
import pty
import threading
import select

# --- Configuration ---
DOWNSAMPLE_RATIO = 2 

# --- Constants ---
RAW_START = 0xABCD
RAW_END = 0xDCBA
RAW_SIZE = 37 
OBCI_START_BYTE = 0xA0
OBCI_END_BYTE = 0xC0

# --- Hardware Config ---
INITIAL_BAUD_RATE = 9600
FINAL_BAUD_RATE = 115200
FIRMWARE_BAUD_RATE_INDEX = 0x04
BOARD_USB_IDS = [{'vid': 0x1A86, 'pid': 0x7523}]
BOARD_DESCRIPTIONS = ["USB-SERIAL CH340", "CH340"]

streaming_enabled = False

def find_and_open_board():
    print("Searching for Cerelog Board...")
    ports = serial.tools.list_ports.comports()
    candidate_ports = [p.device for p in ports if (p.vid and p.pid and {'vid': p.vid, 'pid': p.pid} in BOARD_USB_IDS)]
    if not candidate_ports: candidate_ports = [p.device for p in ports]

    for port_name in candidate_ports:
        print(f"Testing {port_name}...")
        ser = None
        try:
            ser = serial.Serial(port_name, INITIAL_BAUD_RATE, timeout=2)
            time.sleep(3)
            if ser.in_waiting: ser.read(ser.in_waiting)

            ts = int(time.time())
            payload = struct.pack('>BI', 0x02, ts) + bytes([0x01, FIRMWARE_BAUD_RATE_INDEX])
            chk = sum(payload) & 0xFF
            pkt = struct.pack('>BB', 0xAA, 0xBB) + payload + struct.pack('>B', chk) + struct.pack('>BB', 0xCC, 0xDD)
            
            ser.write(pkt)
            time.sleep(0.1)
            ser.baudrate = FINAL_BAUD_RATE
            time.sleep(0.5)
            ser.reset_input_buffer()

            if ser.read(RAW_SIZE * 5):
                print(f"SUCCESS on {port_name}")
                return ser
            ser.close()
        except:
            if ser: ser.close()
    return None

def gui_command_listener(master_fd):
    global streaming_enabled
    
    # === UPDATED IDENTITY STRING ===
    # Added \r\n to match exact serial terminal behavior
    ID_STRING = b"OpenBCI V3 8-16 channel\r\nOn Board ADS1299 Device ID: 0x3E\r\n$$$"

    while True:
        try:
            r, _, _ = select.select([master_fd], [], [], 0.1)
            if master_fd in r:
                cmd_bytes = os.read(master_fd, 1024)
                for byte in cmd_bytes:
                    char = chr(byte)
                    if char == 'v':
                        print("[GUI] Reset (v) -> Sending ID")
                        streaming_enabled = False
                        os.write(master_fd, ID_STRING)
                    elif char == 'b':
                        print("[GUI] Start (b)")
                        streaming_enabled = True
                    elif char == 's':
                        print("[GUI] Stop (s)")
                        streaming_enabled = False
                    # Acknowledge commands
                    elif char in 'xX12345678!@#$%^&*()qwertyuiop': 
                        os.write(master_fd, b',') 
        except OSError:
            break

def main():
    ser = find_and_open_board()
    if not ser: return

    master_fd, slave_fd = pty.openpty()
    virtual_port = os.ttyname(slave_fd)
    
    threading.Thread(target=gui_command_listener, args=(master_fd,), daemon=True).start()

    print("\n" + "="*60)
    print(f"VIRTUAL DONGLE ACTIVE AT: {virtual_port}")
    print("="*60)
    print("COPY THIS PATH AND PASTE IT INTO PROCESSING CODE")
    print("="*60 + "\n")
    
    buffer = bytearray()
    start_marker = RAW_START.to_bytes(2, 'big')
    end_marker = RAW_END.to_bytes(2, 'big')
    packet_counter = 0
    sample_index = 0

    try:
        while True:
            if ser.in_waiting:
                buffer.extend(ser.read(ser.in_waiting))
            else:
                time.sleep(0.0001)

            while True:
                start_idx = buffer.find(start_marker)
                if start_idx == -1:
                    if len(buffer) > 0: buffer = buffer[-1:]
                    break
                if len(buffer) < start_idx + RAW_SIZE: break

                potential_packet = buffer[start_idx : start_idx + RAW_SIZE]
                if potential_packet.endswith(end_marker):
                    packet_counter += 1
                    if streaming_enabled and (packet_counter % DOWNSAMPLE_RATIO == 0):
                        eeg_data = potential_packet[10:34]
                        out_pkt = bytearray([OBCI_START_BYTE])
                        out_pkt.append(sample_index)
                        out_pkt.extend(eeg_data)
                        out_pkt.extend([0]*6)
                        out_pkt.append(OBCI_END_BYTE)
                        os.write(master_fd, out_pkt)
                        sample_index = (sample_index + 1) % 256
                    buffer = buffer[start_idx + RAW_SIZE:]
                    continue
                buffer = buffer[start_idx + 1:]
    except KeyboardInterrupt:
        print("Stopping...")
    finally:
        ser.close()
        os.close(master_fd)
        os.close(slave_fd)

if __name__ == "__main__":
    main()