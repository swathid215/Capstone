@echo off
cd /d c:\Users\91953\OneDrive\Desktop\Jival\PES\Capstone\sumo\auto_tool
python -m pip install sumolib networkx --quiet
python network_repair.py network.net.xml --output cleaned_network.net.xml
