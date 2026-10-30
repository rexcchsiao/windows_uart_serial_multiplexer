import sys
import time
import threading
import logging
import serial
import serial.tools.list_ports

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(threadName)s] %(levelname)s: %(message)s"
)

# ======================= Communication Parameters =======================
DEFAULT_BAUDRATE = 115200       # Default baud rate
PHYSICAL_READ_TIMEOUT = 0.02    # Non-blocking read timeout for physical port (sec)
CLIENT_WRITE_TIMEOUT = 0.005    # Write timeout for virtual ports (sec) to prevent hanging
INPUT_LEASE_TIME = 1.5          # Exclusive input lease time for a single user (sec)
RECONNECT_INTERVAL = 1.0        # Reconnect retry interval after disconnection (sec)
# ========================================================================

class RobustConsoleChannel:
    """Serial channel engine with auto-reconnect, non-blocking broadcast, and input scheduling"""
    def __init__(self, channel_name, physical_port, virtual_ports, baudrate=DEFAULT_BAUDRATE):
        self.channel_name = channel_name
        self.physical_port = physical_port
        self.virtual_ports = virtual_ports
        self.baudrate = baudrate
        
        self.running = True
        self.is_connected = False
        self.phy_ser = None

        # Keyboard input lease management
        self.tx_lock = threading.Lock()
        self.active_client = None
        self.last_client_tx_time = 0

        # Open all virtual endpoints
        self.client_serials = {}
        for port_name in self.virtual_ports:
            try:
                ser = serial.Serial(
                    port_name,
                    self.baudrate,
                    timeout=0.01,
                    write_timeout=CLIENT_WRITE_TIMEOUT
                )
                self.client_serials[port_name] = ser
                t = threading.Thread(
                    target=self._client_reader_thread,
                    args=(port_name, ser),
                    name=f"{channel_name}-Tx-{port_name}",
                    daemon=True
                )
                t.start()
                logging.info(f"[{self.channel_name}] Mounted virtual endpoint: {port_name}")
            except Exception as e:
                logging.error(f"[{self.channel_name}] Failed to open virtual endpoint {port_name}. Check if com0com is properly configured: {e}")

        # Start physical port monitoring and RX thread
        self.worker_thread = threading.Thread(
            target=self._phy_monitor_and_rx_thread,
            name=f"{channel_name}-PhyEngine",
            daemon=True
        )
        self.worker_thread.start()

    def _open_physical_port(self):
        """Attempt to open the physical serial port"""
        try:
            self.phy_ser = serial.Serial(
                self.physical_port,
                self.baudrate,
                timeout=PHYSICAL_READ_TIMEOUT,
                write_timeout=0.1
            )
            self.is_connected = True
            logging.info(f"[{self.channel_name}] Successfully connected to physical port: {self.physical_port}")
            return True
        except (serial.SerialException, OSError):
            self.is_connected = False
            return False

    def _phy_monitor_and_rx_thread(self):
        """Physical port health monitor and RX loop (supports hot-plugging)"""
        while self.running:
            if not self.is_connected:
                # Disconnected state: continuously retry connection
                if self._open_physical_port():
                    time.sleep(0.1)
                else:
                    time.sleep(RECONNECT_INTERVAL)
                    continue

            # Connected state: perform non-blocking data reads
            try:
                waiting = self.phy_ser.in_waiting
                data = self.phy_ser.read(waiting if waiting > 0 else 1)
                if data:
                    self._broadcast_to_clients(data)
            except (serial.SerialException, OSError) as e:
                # Hardware disconnected or driver failure detected
                logging.warning(f"[{self.channel_name}] Physical port {self.physical_port} disconnected or error: {e}. Entering auto-reconnect mode...")
                self.is_connected = False
                try:
                    if self.phy_ser:
                        self.phy_ser.close()
                except Exception:
                    pass
                time.sleep(RECONNECT_INTERVAL)

    def _client_reader_thread(self, client_id, ser):
        """Read client keystrokes and forward to physical hardware"""
        while self.running:
            try:
                waiting = ser.in_waiting
                data = ser.read(waiting if waiting > 0 else 1)
                if not data:
                    continue

                if not self.is_connected:
                    # Discard input if physical port is offline to prevent buffer bloat
                    continue

                now = time.time()
                with self.tx_lock:
                    # Ctrl+C (0x03) has highest interrupt priority
                    if b"\x03" in data:
                        self.active_client = client_id
                        self.last_client_tx_time = now
                        self._safe_phy_write(data)
                        continue

                    # Input lease scheduling: prevent interleaved typing from multiple users
                    if self.active_client is not None:
                        if (now - self.last_client_tx_time) > INPUT_LEASE_TIME:
                            self.active_client = client_id
                        elif self.active_client != client_id:
                            continue
                    else:
                        self.active_client = client_id

                    self.last_client_tx_time = now
                    self._safe_phy_write(data)

            except Exception as e:
                logging.error(f"[{self.channel_name}][{client_id}] Input channel error: {e}")
                break

    def _safe_phy_write(self, data):
        """Safely write to physical port, intercepting write exceptions"""
        try:
            if self.phy_ser and self.is_connected:
                self.phy_ser.write(data)
        except (serial.SerialException, OSError) as e:
            logging.warning(f"[{self.channel_name}] Hardware write failed: {e}")
            self.is_connected = False

    def _broadcast_to_clients(self, data):
        """Broadcast output to all virtual endpoints, skipping unopened ones"""
        for port_name, ser in self.client_serials.items():
            try:
                ser.write(data)
            except serial.SerialTimeoutException:
                pass
            except Exception:
                pass

    def stop(self):
        self.running = False
        if self.phy_ser:
            try:
                self.phy_ser.close()
            except Exception:
                pass


def scan_and_select_ports():
    """Scan available serial ports and prompt user via interactive menu"""
    print("\n==================================================")
    print("     Serial Multiplexer - Auto Port Scanner")
    print("==================================================")

    ports = list(serial.tools.list_ports.comports())
    if not ports:
        print("[!] No available COM Ports detected. Please check hardware connections.")
        sys.exit(1)

    print(f"Detected {len(ports)} port(s):\n")
    for idx, p in enumerate(ports):
        # Highlight com0com virtual ports for easier user distinction
        is_vport = "com0com" in p.description.lower() or "cnca" in p.hwid.lower() or "cncb" in p.hwid.lower()
        tag = "[Virtual/com0com]" if is_vport else "[Physical/USB]"
        print(f"  [{idx + 1}] {p.device:<8} {tag:<18} | Description: {p.description}")

    def prompt_selection(prompt_text, exclude_choice=None):
        while True:
            choice = input(f"\n{prompt_text} (Enter 1~{len(ports)} or type COM name manually): ").strip()
            if choice.isdigit():
                c_idx = int(choice) - 1
                if 0 <= c_idx < len(ports):
                    selected = ports[c_idx].device
                    if selected == exclude_choice:
                        print("[!] This port is already selected for Channel 1. Please choose another!")
                        continue
                    return selected
            upper_choice = choice.upper()
            if upper_choice.startswith("COM"):
                if upper_choice == exclude_choice:
                    print("[!] This port is already selected for Channel 1. Please choose another!")
                    continue
                return upper_choice
            print("[!] Invalid input, please try again.")

    ch1_port = prompt_selection(">> Select the physical port to bind for [Channel 1 (e.g., UART TX/RX)]")
    ch2_port = prompt_selection(">> Select the physical port to bind for [Channel 2 (e.g., UART TX/RX)]", exclude_choice=ch1_port)

    baud_input = input(f">> Set Baudrate [Default {DEFAULT_BAUDRATE}]: ").strip()
    baudrate = int(baud_input) if baud_input.isdigit() else DEFAULT_BAUDRATE

    return ch1_port, ch2_port, baudrate


if __name__ == "__main__":
    ch1_phy, ch2_phy, baudrate = scan_and_select_ports()

    ch1_virtuals = ["COM121", "COM122", "COM123", "COM124", "COM125"]
    ch2_virtuals = ["COM131", "COM132", "COM133", "COM134", "COM135"]

    print("\n--------------------------------------------------")
    print(f"Channel 1: Physical {ch1_phy} -> Virtual Listen COM121~125 (Client Connects to COM21~25)")
    print(f"Channel 2: Physical {ch2_phy} -> Virtual Listen COM131~135 (Client Connects to COM31~35)")
    print(f"Baudrate:  {baudrate} bps")
    print("--------------------------------------------------\n")

    try:
        engine_ch1 = RobustConsoleChannel("CH1", ch1_phy, ch1_virtuals, baudrate)
        engine_ch2 = RobustConsoleChannel("CH2", ch2_phy, ch2_virtuals, baudrate)

        logging.info("Dual-channel proxy server is running. Supports auto-reconnect (Press Ctrl+C to stop).")
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\n[*] Safely shutting down serial proxy service...")
        engine_ch1.stop()
        engine_ch2.stop()
        print("[*] Service terminated.")
