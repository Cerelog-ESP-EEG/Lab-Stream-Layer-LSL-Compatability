import serial
import serial.tools.list_ports
import struct
import time
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# --- Configuration ---
# The HTTP Port the GUI will "find" us on
EMULATOR_HTTP_PORT = 3000 

INITIAL_BAUD_RATE = 9600
FINAL_BAUD_RATE = 115200
FIRMWARE_BAUD_RATE_INDEX = 0x04

# --- Serial/Board Constants ---
DATA_PACKET_START_MARKER = 0xABCD
DATA_PACKET_END_MARKER = 0xDCBA
DATA_PACKET_TOTAL_SIZE = 37
HANDSHAKE_START_MARKER_1 = 0xAA
HANDSHAKE_END_MARKER_1 = 0xCC
PACKET_IDX_LENGTH = 2
PACKET_IDX_CHECKSUM = 34
ADS1299_NUM_CHANNELS = 8
ADS1299_NUM_STATUS_BYTES = 3
ADS1299_BYTES_PER_CHANNEL = 3
BOARD_USB_IDS = [{'vid': 0x1A86, 'pid': 0x7523}]
BOARD_DESCRIPTIONS = ["USB-SERIAL CH340", "CH340"]

# --- Global State for Threading ---
streaming_active = False
gui_tcp_config = {"ip": None, "port": None, "output": "json"}
data_queue = [] # Shared buffer between Serial and TCP
queue_lock = threading.Lock()

# ==============================================================================
#  1. SERIAL CONNECTION (Your original, working logic)
# ==============================================================================
def find_and_open_board():
    print("Searching for Cerelog Board...")
    ports = serial.tools.list_ports.comports()
    candidate_ports = [
        p.device for p in ports 
        if (p.vid and p.pid and {'vid': p.vid, 'pid': p.pid} in BOARD_USB_IDS) or 
           (p.description and any(desc.lower() in p.description.lower() for desc in BOARD_DESCRIPTIONS))
    ]
    if not candidate_ports: candidate_ports = [p.device for p in ports]

    for port_name in candidate_ports:
        print(f"--- Testing port: {port_name} ---")
        ser = None
        try:
            ser = serial.Serial(port_name, INITIAL_BAUD_RATE, timeout=2)
            time.sleep(3) # Wait for reset
            if ser.in_waiting > 0: ser.read(ser.in_waiting)

            print("Sending handshake...")
            current_unix_time = int(time.time())
            checksum_payload = struct.pack('>BI', 0x02, current_unix_time) + bytes([0x01, FIRMWARE_BAUD_RATE_INDEX])
            checksum = sum(checksum_payload) & 0xFF
            handshake_packet = struct.pack('>BB', HANDSHAKE_START_MARKER_1, 0xBB) + checksum_payload + struct.pack('>B', checksum) + struct.pack('>BB', HANDSHAKE_END_MARKER_1, 0xDD)
            
            ser.write(handshake_packet)
            time.sleep(0.1)
            ser.baudrate = FINAL_BAUD_RATE
            time.sleep(0.5)
            ser.reset_input_buffer()

            bytes_received = ser.read(DATA_PACKET_TOTAL_SIZE * 5)
            if bytes_received and DATA_PACKET_START_MARKER.to_bytes(2, 'big') in bytes_received:
                print(f"SUCCESS! Board found on: {port_name}")
                return ser
            else:
                ser.close()
        except:
            if ser: ser.close()
    return None

# ==============================================================================
#  2. SERIAL READER THREAD
#     Reads USB, parses packets, puts them in the queue
# ==============================================================================
def serial_worker(ser):
    global data_queue
    buffer = bytearray()
    start_marker = DATA_PACKET_START_MARKER.to_bytes(2, 'big')
    end_marker = DATA_PACKET_END_MARKER.to_bytes(2, 'big')
    
    print("Serial Worker Started.")
    
    while True:
        if ser.in_waiting > 0:
            buffer.extend(ser.read(ser.in_waiting))
        else:
            time.sleep(0.001)

        while True:
            start_idx = buffer.find(start_marker)
            if start_idx == -1:
                if len(buffer) > 0: buffer = buffer[-1:]
                break
            if len(buffer) < start_idx + DATA_PACKET_TOTAL_SIZE:
                break

            potential_packet = buffer[start_idx : start_idx + DATA_PACKET_TOTAL_SIZE]
            if potential_packet.endswith(end_marker):
                payload = potential_packet[PACKET_IDX_LENGTH:PACKET_IDX_CHECKSUM]
                if (sum(payload) & 0xFF) == potential_packet[PACKET_IDX_CHECKSUM]:
                    
                    # --- Parse Valid Packet ---
                    ads_data = potential_packet[7:34]
                    row_data = []
                    for ch in range(ADS1299_NUM_CHANNELS):
                        idx = ADS1299_NUM_STATUS_BYTES + ch * ADS1299_BYTES_PER_CHANNEL
                        raw_bytes = ads_data[idx : idx + ADS1299_BYTES_PER_CHANNEL]
                        # OpenBCI WiFi JSON expects Raw Integers
                        val = int.from_bytes(raw_bytes, byteorder='big', signed=True)
                        row_data.append(val)

                    # Add Aux (0,0,0) and Timestamp
                    row_data.extend([0, 0, 0])
                    row_data.append(time.time() * 1000)

                    # Add to Shared Queue
                    with queue_lock:
                        data_queue.append(row_data)
                        # Keep queue small to prevent lag
                        if len(data_queue) > 500: data_queue.pop(0)

                    buffer = buffer[start_idx + DATA_PACKET_TOTAL_SIZE:]
                    continue
            
            buffer = buffer[start_idx + 1:]

# ==============================================================================
#  3. TCP STREAMER THREAD
#     Connects to GUI's TCP Server and pushes data
# ==============================================================================
def tcp_worker():
    global streaming_active, gui_tcp_config, data_queue
    
    print("TCP Worker waiting for config...")
    
    while True:
        # Wait until GUI sends /stream/start
        if not streaming_active or not gui_tcp_config["ip"]:
            time.sleep(0.1)
            continue
            
        try:
            print(f"Connecting TCP to GUI at {gui_tcp_config['ip']}:{gui_tcp_config['port']}...")
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect((gui_tcp_config['ip'], gui_tcp_config['port']))
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            
            packet_count = 0
            chunk_buffer = []

            while streaming_active:
                # Get data from queue
                with queue_lock:
                    if len(data_queue) > 0:
                        chunk_buffer.extend(data_queue)
                        data_queue.clear()
                
                # If we have data, send it in chunks of ~10 samples
                if len(chunk_buffer) >= 10:
                    send_chunk = chunk_buffer[:10]
                    chunk_buffer = chunk_buffer[10:]
                    packet_count += 1
                    
                    msg = {
                        "chunk": send_chunk,
                        "count": packet_count
                    }
                    
                    # OpenBCI GUI expects newline-delimited JSON
                    json_str = json.dumps(msg) + "\r\n"
                    s.sendall(json_str.encode('utf-8'))
                else:
                    time.sleep(0.005) # Don't burn CPU
                    
        except Exception as e:
            print(f"TCP Stream Error: {e}")
            streaming_active = False # Stop if connection breaks
        finally:
            s.close()
            print("TCP Socket Closed.")

# ==============================================================================
#  4. HTTP SERVER (The "WiFi Shield" Brain)
#     Answers /board, /tcp, /stream/start
# ==============================================================================
class ShieldEmulatorHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global streaming_active
        
        if self.path == '/board':
            # GUI asks: "Who are you?"
            print(f"GUI Requested: {self.path} (Handshake)")
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            # We pretend to be a Cyton with 8 channels
            resp = {
                "board_type": "cyton",
                "num_channels": 8,
                "connected": True,
                "gains": [24]*8
            }
            self.wfile.write(json.dumps(resp).encode())
            
        elif self.path == '/stream/start':
            # GUI says: "GO!"
            print("GUI Requested: START STREAMING")
            streaming_active = True
            self.send_response(200)
            self.end_headers()
            
        elif self.path == '/stream/stop':
            # GUI says: "STOP!"
            print("GUI Requested: STOP STREAMING")
            streaming_active = False
            self.send_response(200)
            self.end_headers()
            
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        global gui_tcp_config
        
        if self.path == '/tcp':
            # GUI says: "Send data to this IP/Port"
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            config = json.loads(post_data)
            
            print(f"GUI Configuration Received: {config}")
            
            gui_tcp_config["ip"] = config.get("ip")
            gui_tcp_config["port"] = config.get("port")
            gui_tcp_config["output"] = config.get("output", "json")
            
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"connected": True, "url": f"tcp://{config['ip']}:{config['port']}"}).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        return # Silence standard HTTP logs to keep console clean

# ==============================================================================
#  MAIN EXECUTION
# ==============================================================================
def main():
    # 1. Connect Hardware
    ser = find_and_open_board()
    if not ser:
        print("CRITICAL ERROR: No Board Found.")
        return

    # 2. Start Serial Thread
    t_serial = threading.Thread(target=serial_worker, args=(ser,), daemon=True)
    t_serial.start()

    # 3. Start TCP Streamer Thread (Starts paused)
    t_tcp = threading.Thread(target=tcp_worker, daemon=True)
    t_tcp.start()

    # 4. Start HTTP Server
    server_address = ('0.0.0.0', EMULATOR_HTTP_PORT)
    httpd = HTTPServer(server_address, ShieldEmulatorHandler)
    
    print(f"\n>>> WIFI EMULATOR RUNNING on PORT {EMULATOR_HTTP_PORT} <<<")
    print(f"1. Open Standard OpenBCI GUI")
    print(f"2. Select CYTON -> WIFI -> MANUAL")
    print(f"3. Enter IP: 127.0.0.1")
    print(f"4. Enter Port: {EMULATOR_HTTP_PORT}")
    print(f"5. Click 'START SESSION'")
    
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping...")
        if ser: ser.close()

if __name__ == "__main__":
    main()