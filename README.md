# Multi-Client Dual UART Serial Multiplexer (Windows)

[![Python Version](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/platform-Windows-lightgrey.svg)](https://microsoft.com/windows)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

A dual-channel UART serial port multiplexer/proxy designed specifically for Windows environments. By utilizing the `com0com` virtual serial port driver alongside a non-blocking streaming proxy architecture, this tool completely bypasses the Windows Win32 API's `ACCESS_DENIED` limitation for exclusive COM port access. It allows up to 5 applications (e.g., Tera Term, PuTTY, automated test scripts) to simultaneously share a single physical serial port for bi-directional console access.

---

## Core Design Principles

### 1. Why Can't Windows Share COM Ports Natively?
The Windows kernel uses the `CreateFile` API with an exclusive flag when opening a COM port. Once software opens a serial port, the underlying operating system will explicitly deny access requests from any subsequent programs.

### 2. Architecture and Solution Topology
```
[ Applications / Users (Clients) ]
  ├── Tera Term 1 ──> COM21 ──┐ (com0com virtual crossover cables)
  ├── Tera Term 2 ──> COM22 ──┼──> COM121~125 ──┐
  └── Tera Term 5 ──> COM25 ──┘                 │
                                                ▼
                                    [ Python Proxy / Broker ]
                                       - Full-duplex streaming proxy
                                       - Console typing lease management (1.5s)
                                       - Physical port auto-reconnect (Hot-Plug)
                                                │
                                                ▼
                                    Physical Serial Port COM9 / COM10
                                                │
                                                ▼
                                    [ Embedded Board / UART Device ]
```
* **Virtual Port Pairs (`com0com`):** Pairs of virtual ports are created at the system level (e.g., `COM21` ↔ `COM121`).
* **Central Proxy (Python):** Exclusively opens the physical ports and listens to all `COM12x` / `COM13x` virtual endpoints on the backend.
* **Safe Output Broadcast:** Data output from the physical hardware is broadcasted to all connected virtual ports with zero delay. If a virtual port is not currently connected to an application, the proxy will safely ignore it, preventing buffer crashes.
* **Input Lease Anti-Collision (Console Lease):** When a user starts typing, they temporarily gain exclusive input rights for 1.5 seconds. This prevents characters from scrambling if multiple users type simultaneously, while still allowing `Ctrl+C` commands immediate interrupt priority.

---

## System Requirements

* **OS:** Windows 10 / Windows 11 (64-bit)
* **Python Version:** Python 3.8 or above (3.10+ recommended)
* **Virtual Serial Port Driver:** Null-modem emulator (`com0com`) v3.0.0.0 or v2.2.2.0

---

## Installation Guide

### Step 1: Install the com0com Virtual Serial Port Driver

1. Download the [latest com0com installer](https://sourceforge.net/projects/com0com/) from SourceForge (If using 64-bit Windows and encountering digital signature blocks, download a version with Test Signing support or disable driver signature enforcement).
2. After installation, launch PowerShell as an **Administrator**.
3. Navigate to the com0com installation directory and batch-create 10 pairs of virtual ports:
   ```powershell
   cd "C:\Program Files (x86)\com0com"

   # Create Channel 1 links (Mapped to COM9)
   .\setupc install PortName=COM21 PortName=COM121
   .\setupc install PortName=COM22 PortName=COM122
   .\setupc install PortName=COM23 PortName=COM123
   .\setupc install PortName=COM24 PortName=COM124
   .\setupc install PortName=COM25 PortName=COM125

   # Create Channel 2 links (Mapped to COM10)
   .\setupc install PortName=COM31 PortName=COM131
   .\setupc install PortName=COM32 PortName=COM132
   .\setupc install PortName=COM33 PortName=COM133
   .\setupc install PortName=COM34 PortName=COM134
   .\setupc install PortName=COM35 PortName=COM135
   ```
4. Verify the installation:
   ```powershell
   .\setupc list
   ```
   *Alternatively, open the Windows "Device Manager" and check for the `com0com - serial port emulator` node.*

### Step 2: Download the Project and Create a Virtual Environment (venv)

```powershell
# 1. Clone the repository
git clone <your-repo-url>
cd <project-directory>

# 2. Create a Python virtual environment
python -m venv venv

# 3. Activate the virtual environment
.\venv\Scripts\Activate.ps1
# If you encounter a script restriction error, run: Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass

# 4. Install dependencies
pip install -r requirements.txt
```

> **`requirements.txt` contents:**
> ```text
> pyserial>=3.5
> ```

---

## Usage Instructions

### 1. Launch the Proxy Broker Service

Ensure that all software currently occupying the physical COM ports is closed, then run the following in the activated `(venv)` terminal:

```powershell
python .\interactive_uart_proxy.py
```

### 2. Interactive Port Selection

Upon startup, the program will automatically scan the system's available serial ports:

```text
==================================================
     Serial Multiplexer - Auto Port Scanner
==================================================
Detected 4 port(s):

  [1] COM9     [Physical/USB]     | Description: USB Serial Port (COM9)
  [2] COM10    [Physical/USB]     | Description: USB Serial Port (COM10)
  [3] COM21    [Virtual/com0com]  | Description: com0com - serial port emulator CNCB0
  [4] COM31    [Virtual/com0com]  | Description: com0com - serial port emulator CNCB1

>> Select the physical port to bind for [Channel 1 (e.g., UART TX/RX)] (Enter 1~4 or type COM name manually): 1
>> Select the physical port to bind for [Channel 2 (e.g., UART TX/RX)] (Enter 1~4 or type COM name manually): 2
>> Set Baudrate [Default 115200]: 115200
```

After confirming your selection, the proxy service will remain active in the background.

### 3. Client Connection Mapping

Configure your applications (Tera Term, PuTTY, etc.) to connect to the corresponding **front-end virtual ports** based on the table below. Set the baud rate to match your hardware (e.g., 115200):

| Physical Channel | Target Device | Terminal Client Endpoint | Max Concurrent Connections |
| :--- | :--- | :--- | :--- |
| **Channel 1** | Physical Port 1 (e.g., COM9) | **`COM21`, `COM22`, `COM23`, `COM24`, `COM25`** | 5 Users / Apps |
| **Channel 2** | Physical Port 2 (e.g., COM10) | **`COM31`, `COM32`, `COM33`, `COM34`, `COM35`** | 5 Users / Apps |

---

## Troubleshooting and Auto-Recovery

* **Physical Cable Unexpectedly Unplugged (USB Disconnect):**
  * The proxy script features auto-fault detection. The terminal will not crash when unplugged; the log will simply display `Entering auto-reconnect mode...`.
  * Reconnecting the USB cable will trigger the proxy to automatically locate the COM port and restore the connection without needing to restart the Python script manually.
* **Tera Term displays `Cannot open COMxxx`:**
  * Ensure you are connecting to `COM21~25` or `COM31~35`, and NOT the backend's exclusive `COM121~135`.
  * If it still refuses to open, run `setupc list` in an Administrator PowerShell to verify that the virtual endpoints have been successfully created.
* **PowerShell `Activate.ps1` execution failed:**
  * Run: `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` to temporarily bypass execution policies for the current window.
