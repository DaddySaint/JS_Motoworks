#!/bin/bash

echo "STARTING JS MOTOWORKS"

echo "Activating Python Virtual Environment"
source venv/Scripts/activate  # 'source venv/bin/activate' MacBook )

echo "Starting Local Server (Flask)"
python app.py &  # 'python3 app.py' (macbook)

# load flask 3 secs
sleep 3

# Ngrok Tunnel
echo "Opening Internet Tunnel via Ngrok"

./ngrok.exe http --domain=junior-equate-operator.ngrok-free.dev 5000 # (Tanggalin ang .exe if MacBook na)
