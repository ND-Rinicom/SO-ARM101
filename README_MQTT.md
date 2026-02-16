# SO-ARM101 MQTT Teleoperation

**Minimal** codebase for controlling SO-ARM101 follower via MQTT from a leader arm.

## Features

- **Jump Protection**: Follower checks safety locally (no network latency)
- **Calibration Support**: Uses standard LeRobot calibration files
- **Robust**: Works even with slow/unreliable networks  
- **Minimal**: ~130KB (perfect for Raspberry Pi with limited space)

## Setup

### 1. Create Virtual Environment (Recommended)

On both PC and Raspberry Pi:

```bash
# Create virtual environment
python3 -m venv ~/lerobot-venv

# Activate virtual environment
source ~/lerobot-venv/bin/activate

# Verify Python version
python --version
```

### 2. Install Dependencies

On both PC and Raspberry Pi (with venv activated):

```bash
pip install paho-mqtt feetech-servo-sdk pyserial deepdiff tqdm numpy huggingface-hub
```

### 3. Transfer to Raspberry Pi

Copy the project from PC to Raspberry Pi:

```bash
# From your PC:
scp -r /<PROJECT_PATH>/SO-ARM101 <PI_USERNAME>@<PI_IP_ADDRESS>:~/SO-ARM101
```

### 4. Find Serial Port

Before calibrating, identify which port your SO-ARM is connected to:

**Linux (PC and Raspberry Pi):**
Unplug the arm, run `ls /dev/ttyACM*`, then plug it back in and run the command again to see which device appears.

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

### 7. Configure Scripts

Edit the configuration values at the top of each script:

**scripts/follower_mqtt_controller.py** (run on Pi):
```python
FOLLOWER_PORT = "/dev/ttyACM0"           # Your follower serial port
FOLLOWER_ID = "so_follower"                     # Your calibration ID
MQTT_BROKER = "192.168.1.107"            # Your MQTT broker IP
MAX_RELATIVE_TARGET = 20.0               # Jump protection limit
```

**scripts/leader_mqtt_sender.py** (run on PC):
```python
LEADER_PORT = "/dev/ttyACM0"             # Your leader serial port
LEADER_ID = "so_leader"                        # Your calibration ID
MQTT_BROKER = "192.168.1.107"            # Your MQTT broker IP
FPS = 60                                  # Control loop frequency
```

## Usage

### 1. Start Follower Controller (on Raspberry Pi)

```bash
cd /home/nialldorrington/Documents/SO-ARM101
python scripts/follower_mqtt_controller.py
```

You should see:
```
Follower arm connected
Connected to MQTT broker
Follower controller started. Waiting for targets...
```

### 2. Start Leader Sender (on PC)

```bash
cd /home/nialldorrington/Documents/SO-ARM101
python scripts/leader_mqtt_sender.py
```

You should see:
```
Leader arm connected
Connected to MQTT broker
Leader sender started at 60 FPS
Move the leader arm to control the follower
```

### 3. Control!

Move the leader arm - the follower will follow safely with jump protection.

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

# start video 

on pi 

./scripts/follower_mqtt_controller.py --camera /dev/video0

host from pi 
python3 -m http.server 8001 --directory ~
