# Remote IoT Dashboard using Raspberry Pi & Node-RED

## Overview

This project is a remote IoT monitoring and control dashboard developed using Raspberry Pi and Node-RED.

The dashboard enables remote monitoring and control of field devices such as motors and valves through a web-based interface.

The system uses Node-RED workflows for device communication, automation logic, and dashboard visualization.

---

## Features

- Remote motor ON/OFF control
- Valve control and monitoring
- Real-time device status monitoring
- Motor scheduling and automation
- Group-based device control
- MQTT-based communication
- Node-RED dashboard interface
- Secure remote access using Cloudflare Tunnel

---

## Technologies Used

### Hardware
- Raspberry Pi
- STM32-based IoT Controllers
- Motor Control Modules
- Valve Control Modules
- Sensors

### Software
- Node-RED
- MQTT
- JavaScript
- Python
- Cloudflare Tunnel

### Monitoring
- Node-RED Dashboard
- Grafana
- InfluxDB

---

## Project Structure
Remote-IoT-Dashboard

├── Node-RED
│ └── flows.json
│
├── Screenshots
│ ├── motor-control.png
│ ├── motor-monitoring.png
│ └── valve-control.png
│
└── README.md


# Node-RED Flow

The Node-RED flow handles:

- MQTT message processing
- Device command handling
- Motor and valve control logic
- Dashboard data processing
- Real-time device status updates

Flow file:
Node-RED/flows.json



---


# Dashboard Screenshots


## Motor Group Scheduling


This section provides motor scheduling and automation features.


![Motor Group Control](Screenshots/motor-control.png)




## Motor Monitoring & Control


Features:
- Motor status monitoring
- Voltage monitoring
- Current monitoring
- Remote control


![Motor Monitoring](Screenshots/motor-monitoring.png)




## Valve Control


Features:
- Remote valve operation
- Device status feedback


![Valve Control](Screenshots/valve-control.png)


---


# Remote Access


Cloudflare Tunnel is used to provide secure remote access to the Raspberry Pi hosted dashboard without router port forwarding.
