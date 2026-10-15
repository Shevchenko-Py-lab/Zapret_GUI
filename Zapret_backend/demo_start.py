"""Harmless test process: repeatedly sleeps. Stop with Ctrl+C or F01 controller."""
import time
print('Demo process running...', flush=True)
while True:
    time.sleep(1)
