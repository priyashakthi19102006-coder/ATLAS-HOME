import serial
import time

def test_commands():
    ser = serial.Serial('COM5', 921600, timeout=1.0)
    ser.reset_input_buffer()
    
    commands = [
        b'help\n',
        b'HELP\n',
        b'?\n',
        b'status\n',
        b'STATUS\n',
        b'{"type":"status"}\n',
        b'{"cmd":"status"}\n',
        b'{"action":"test"}\n',
        b'TEST\n',
        b'STATE:HAPPY\n',
        b'{"state":"Happy"}\n',
        b'{"text":"hello"}\n',
        b'SPEAK:Hello\n',
    ]

    for cmd in commands:
        ser.reset_input_buffer()
        ser.write(cmd)
        time.sleep(0.3)
        res = ser.read(ser.in_waiting)
        # Filter out audio bytes if high bits or non-ascii
        text_res = res.decode('latin-1', errors='replace')
        print(f"Sent: {cmd.strip()} -> Raw: {len(res)} bytes")
        # Print any ascii-like strings
        ascii_parts = [p for p in text_res.split('\r\n') if any(c.isalnum() for c in p)]
        if ascii_parts:
            print("   ASCII lines:", ascii_parts)

    ser.close()

if __name__ == "__main__":
    test_commands()
