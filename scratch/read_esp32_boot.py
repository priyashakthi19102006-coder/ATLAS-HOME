import serial
import time
import re

def read_boot():
    ser = serial.Serial('COM5', 921600, timeout=2.0)
    ser.dtr = False
    ser.rts = True
    time.sleep(0.1)
    ser.rts = False
    time.sleep(1.0)
    
    boot_data = b""
    start = time.time()
    while time.time() - start < 3.0:
        if ser.in_waiting:
            boot_data += ser.read(ser.in_waiting)
        time.sleep(0.05)
    ser.close()

    # Save raw bytes to file
    with open("scratch/esp32_boot.bin", "wb") as f:
        f.write(boot_data)
        
    # Extract printable ascii strings
    strings = re.findall(b"[a-zA-Z0-9 _.,:;!@#$%^&*()+\\-\\/=<>?~`'\"]{4,}", boot_data)
    print(f"Extracted {len(strings)} printable strings:")
    for s in strings:
        print("  ->", s.decode('ascii', errors='ignore'))

if __name__ == "__main__":
    read_boot()
