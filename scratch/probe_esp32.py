import serial
import time
import json

def probe():
    print("Opening COM5 at 921600...")
    ser = serial.Serial('COM5', 921600, timeout=0.5)
    ser.reset_input_buffer()
    
    # Listen passively first
    time.sleep(1.0)
    passive = ser.read(ser.in_waiting)
    print("Passive bytes count:", len(passive))
    if passive:
        print("Passive sample (raw):", passive[:200])
        try:
            print("Passive sample (utf-8):", passive.decode('utf-8', errors='replace')[:200])
        except Exception:
            pass

    # Probe 1: JSON ping
    ser.write(b'{"type":"ping"}\n')
    time.sleep(0.5)
    resp1 = ser.read(ser.in_waiting)
    print("Probe 1 (JSON ping) resp:", resp1)

    # Probe 2: JSON status
    ser.write(b'{"command":"status"}\n')
    time.sleep(0.5)
    resp2 = ser.read(ser.in_waiting)
    print("Probe 2 (JSON status) resp:", resp2)

    # Probe 3: Text PING
    ser.write(b'PING\n')
    time.sleep(0.5)
    resp3 = ser.read(ser.in_waiting)
    print("Probe 3 (Text PING) resp:", resp3)

    ser.close()
    print("Closed COM5.")

if __name__ == "__main__":
    probe()
