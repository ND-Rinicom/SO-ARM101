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

To deactivate later: `deactivate`

### 2. Install Dependencies

On both PC and Raspberry Pi (with venv activated):

```bash
pip install paho-mqtt feetech-servo-sdk pyserial draccus deepdiff tqdm numpy huggingface-hub
```

**Note:** `torch`, `accelerate`, and `datasets` are optional and not required for MQTT control.

### 3. Transfer to Raspberry Pi (if needed)

Copy the project from PC to Raspberry Pi:

```bash
# From your PC:
scp -r /home/nialldorrington/Documents/SO-ARM101 so-101@<PI_IP_ADDRESS>:~/Documents/
```

### 4. Find Serial Port

Before calibrating, identify which port your SO-ARM is connected to:

**Linux (PC and Raspberry Pi):**
```bash
# List all serial devices
ls /dev/ttyACM* /dev/ttyUSB* 2>/dev/null

# Or use Python to list ports
python -m serial.tools.list_ports

# Watch for new devices when plugging in the arm
dmesg | grep -i tty | tail
```

**Common port names:**
- `/dev/ttyACM0` - Most common for SO-ARM
- `/dev/ttyUSB0` - If using USB-to-serial adapter
- `/dev/ttyACM1` - If multiple devices connected

**Tip:** Unplug the arm, run `ls /dev/ttyACM*`, then plug it back in and run the command again to see which device appears.

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

This creates calibration files in `~/.cache/calibration/`.

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
```bash
ffmpeg -f v4l2 -framerate 10 -video_size 1280x720 -i /dev/video0   -an -c:v libx264 -preset veryfast -tune zerolatency -pix_fmt yuv420p   -g 20 -keyint_min 20 -sc_threshold 0   -f rtsp -rtsp_transport tcp rtsp://192.168.1.107:8554/cam
```
