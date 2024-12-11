#!/usr/bin/env python3

from pynput import keyboard
from client import AbletonOSCClient
    
client = AbletonOSCClient("127.0.0.1", 11000)
client.send_message("/live/api/reload")

def handle_keypress(key):
    """
    Handles the logic for each key press based on the 3x3 grid.
    """
    try:
        if key.char == 'q':
            print("Q pressed - handle logic here")
            client.send_message("/syringe/loopControl", [0, 226, .25])
        elif key.char == 'w':
            print("W pressed - handle logic here")
            client.send_message("/syringe/loopControl", [0, 226, 1])
        elif key.char == 'e':
            print("E pressed - handle logic here")
            client.send_message("/syringe/loopControl", [0, 226, 4])
        elif key.char == 'a':
            print("A pressed - handle logic here")
            # Shift left a bar
            client.send_message("/syringe/loopControlStart", [0, 226, -4])
            client.send_message("/syringe/loopControlEnd", [0, 226, -4])
        elif key.char == 's':
            print("S pressed - handle logic here")
            client.send_message("/syringe/loopControl", [0, 226, 16])
        elif key.char == 'd':
            print("D pressed - handle logic here")
            # Shift right a bar
            client.send_message("/syringe/loopControlEnd", [0, 226, 4])
            client.send_message("/syringe/loopControlStart", [0, 226, 4])
        elif key.char == 'z':
            print("Z pressed - handle logic here")
            client.send_message("/syringe/loopControl", [0, 226, .25])
        elif key.char == 'x':
            print("X pressed - handle logic here")
            client.send_message("/syringe/loopControl", [0, 226, .25])
        elif key.char == 'c':
            print("C pressed - handle logic here")
            client.send_message("/syringe/loopControl", [0, 226, .25])
    except AttributeError:
        # Handle special keys if needed
        pass

def on_press(key):
    """
    Callback for key press events.
    """
    handle_keypress(key)

if __name__ == "__main__":
    print("Listening for key presses (Q, W, E, A, S, D, Z, X, C)...")

    with keyboard.Listener(on_press=on_press) as listener:
        listener.join()
