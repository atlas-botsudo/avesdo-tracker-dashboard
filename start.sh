#!/bin/bash
cd "$(dirname "$0")"
pip install flask --quiet 2>/dev/null
python3 server.py
