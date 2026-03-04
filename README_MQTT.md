# SO-ARM101 MQTT Teleoperation

**Minimal** codebase for controlling SO-ARM101 follower via MQTT from a leader arm.

## Features

- **Lightweight**: ~770KB of code (vs 5-10MB+ full LeRobot)
- **Jump Protection**: Follower checks safety locally
- **Calibration Support**: Uses standard LeRobot calibration files

## Setup

### 1. Create Virtual Environment (Recommended)
On both PC and Raspberry Pi create and activate venv:
```bash
python3 -m venv ./lerobot-venv
source ./lerobot-venv/bin/activate
```

### 2. Install Dependencies
On both PC and Raspberry Pi (with venv activated):
```bash
pip install paho-mqtt feetech-servo-sdk pyserial numpy
```

### 3. Transfer to Raspberry Pi
Copy the project from PC to Raspberry Pi:
```bash
# From your PC:
scp -r /<PROJECT_PATH>/ <PI_USERNAME>@<PI_IP_ADDRESS>:~/
```

### 4. Find Serial Port
Before calibrating, identify which port your SO-ARM is connected to:

**Linux (PC and Raspberry Pi):**
Unplug the arm, run `ls /dev/ttyACM*`, then plug it back in and run the command again to see which device appears.

**Optional: Find Camera Device (if using video streaming):**
Unplug the camera, run `ls /dev/video*`, then plug it back in and run the command again to see which device appears (e.g., `/dev/video0`).

### 5. Calibrate Your Arms
You need calibration files for both leader and follower. Run on their respective machines:
```bash
# On Follower Pi (connected to follower arm)
python -c "from lerobot.robots.so_follower import SO101Follower, SO101FollowerConfig; \
f = SO101Follower(SO101FollowerConfig(port='/dev/ttyACM0', id='so_follower')); \
f.connect(); f.calibrate()"

# On Leader PC (connected to leader arm)
python -c "from lerobot.teleoperators.so_leader import SO101Leader, SO101LeaderConfig; \
l = SO101Leader(SO101LeaderConfig(port='/dev/ttyACM0', id='so_leader')); \
l.connect(); l.calibrate()"
```

This creates calibration files in `lerobot/calibrations/`.

### 6. Setup MQTT Broker
Install Mosquitto or use an existing MQTT broker:

```bash
# On any machine (or use cloud broker)
sudo apt install mosquitto mosquitto-clients
sudo systemctl start mosquitto
```

### 7. Set UDP priorities (optional)

If wanting communication from the laptop (leader) to pi (follower) via UDP its a good idea to set the servo comunications as higher priority than the video stream as video packets droping is much better.

On the pi cmd 
```bash
# Wipe existing rules
sudo tc qdisc del dev eth0 root

# Rebuild with pfifo on robot band
sudo tc qdisc add dev eth0 root handle 1: prio
sudo tc qdisc add dev eth0 parent 1:1 handle 10: pfifo
sudo tc qdisc add dev eth0 parent 1:2 handle 20: tbf rate 2mbit burst 32kbit latency 50ms

# Reapply filters
sudo tc filter add dev eth0 protocol ip parent 1:0 prio 1 u32 \
    match ip dport 9000 0xffff flowid 1:1
sudo tc filter add dev eth0 protocol ip parent 1:0 prio 2 u32 \
    match ip dport 5000 0xffff flowid 1:2
```

## Usage

### 1. Start Follower Controller (on Raspberry Pi)

```bash
python scripts/follower_mqtt_controller.py
```
The command above uses `__init__` defaults and does **not** start any camera or HTTP server.

Optional command-line parameters (with comments):
```bash
python scripts/follower_mqtt_controller.py \
  --follower-port /dev/ttyACM0 \            # Serial port for the follower arm
  --follower-id so_follower \               # Calibration ID for the follower arm
  --mqtt-broker 0.0.0.0 \                   # MQTT broker IP or hostname
  --mqtt-port 1883 \                        # MQTT broker port
  --mqtt-topic watchman_robotarm/so-101 \   # MQTT topic for joint commands
  --max-relative-target 20.0 \              # Max per-step joint change
  --fps 24 \                                # Target send frequency
  --use-degrees                             # Use degrees instead of radians
```

You should see:
```
Follower arm connected
Connected to MQTT broker
Follower controller started. Waiting for targets...
```

### 2. Start Leader Sender (on PC)
```bash
python scripts/leader_mqtt_sender.py
```
The command above uses `__init__` defaults.

Optional command-line parameters (with comments):
```bash
python scripts/leader_mqtt_sender.py \
  --leader-port /dev/ttyACM0 \              # Serial port for the leader arm
  --leader-id so_leader \                   # Calibration ID for the leader arm
  --mqtt-broker 192.168.1.107 \             # MQTT broker IP or hostname
  --mqtt-port 1883 \                        # MQTT broker port
  --mqtt-topic watchman_robotarm/so-101 \   # MQTT topic for joint commands
  --fps 24                                  # Control loop frequency (Hz)
  --use-degrees
```

You should see:
```
Leader arm connected
Connected to MQTT broker
Leader sender started at 24 FPS
Move the leader arm to control the follower
```

### 3. Open index.html
Open index.html from the same IP as your front-end nginx server:
```
http://<IP_ADDR>/index.html
```
Optional query parameters (with comments):
```
http://<IP_ADDR>/index.html
?model="so-101" # Robot model name
&xPos=0         # Initial X position
&yPos=0         # Initial Y position
&zPos=0         # Initial Z position
&cameraZPos=2   # Camera Z position
&wireframe=1    # Render in wireframe mode (1 = on, 0 = off)
```
You should see:
Robot arm model at matching the position of your leader arm

### 3. Control!

Move the leader arm - the digital twin and follower will follow safely with jump protection.

## Safety Features

### Hardware Limits
- Set during calibration
- Stored in servo firmware
- Enforced even without software

### Jump Protection
- `max_relative_target` limits position changes per step
- Applied on follower side (no network delay)
- Example: With `max_relative_target=20.0`:
  - Target: 45° → 180° (135° jump)
  - Actual: 45° → 65° (20° jump)
  - Gradually moves to target over multiple steps

### Reconnection Safety
- If connection drops, follower stops moving
- On reconnect, follower re-reads position before accepting new targets
- No dangerous jumps after network interruption

## File Structure

```
SO-ARM101/
├── README.md                           # Main project README
├── README_MQTT.md                      # This file - MQTT teleoperation guide
├── front-end/                          # Web-based control interface (optional)
│   ├── index.html
│   ├── js/
│   └── models/
├── lerobot/                            # Full LeRobot library (from GitHub)
│   ├── robots/                         # Robot implementations
│   │   └── so_follower/               # SO-ARM101 follower
│   ├── teleoperators/                  # Teleoperation implementations
│   │   └── so_leader/                 # SO-ARM101 leader
│   ├── motors/                         # Motor/servo drivers
│   ├── cameras/                        # Camera utilities
│   └── ...                            # Complete LeRobot framework
└── scripts/                            # Custom MQTT control scripts
    ├── follower_mqtt_controller.py     # Run on Pi (follower)
    └── leader_mqtt_sender.py           # Run on PC (leader)
```

## MQTT Message Format

All messages use JSON-RPC 2.0 format:

```json
{
  "jsonrpc": "2.0",
  "id": "unique-message-id",
  "method": "set_joint_angles",
  "timestamp": "2026-01-14T15:27:05Z",
  "params": {
    "units": "degrees",
    "joints": {
      "shoulder_pan.pos": 0,
      "shoulder_lift.pos": 0,
      "elbow_flex.pos": 0,
      "wrist_flex.pos": 0,
      "wrist_roll.pos": 0,
      "gripper.pos": 0
    }
  }
}
```

The `.pos` suffix is automatically stripped by the follower before applying joint angles.
