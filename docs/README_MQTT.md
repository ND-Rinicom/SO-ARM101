# SO-ARM101 MQTT Teleoperation

**Minimal** codebase for controlling SO-ARM101 follower via MQTT from a leader arm.

## Features

- **Lightweight**: ~770KB of code (vs 5-10MB+ full LeRobot)
- **Jump Protection**: Follower checks safety locally
- **Calibration Support**: Uses standard LeRobot calibration files


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
  "id": "unique-message-id",
  "method": "set_follower_joint_angles",
  "timestamp": "2026-01-14T15:27:05Z",
  "params": {
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
