#!/usr/bin/env python3
"""Configure a personal OS image or mounted card before first boot."""
from pathlib import Path
import runpy

if __name__ == "__main__":
    runpy.run_path(str(Path(__file__).with_name("configure-wifi.py")))["main"]()
