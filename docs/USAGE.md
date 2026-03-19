

## Usage

### 1. Make sure venv is activated (Both PC & Pi)
```bash
source lerobot-venv/bin/activate
```

### 2. Start Leader Sender (on PC)

```bash
python scripts/leader.py
```
The command above uses `__init__` defaults.

Optional command-line parameters (with comments):
```bash
python scripts/leader.py \
  --leader-port /dev/ttyACM0 \              # Serial port for the leader arm
  --leader-id so_leader \                   # Calibration ID for the leader arm
  --mqtt-broker <MQTT_BROKER_IP> \          # MQTT broker IP or hostname
  --mqtt-port 1883 \                        # MQTT broker port
  --mqtt-topic watchman_robotarm/so-101 \   # MQTT topic
  --fps 24 \                                # Control loop frequency (Hz)
  --idle-send-interval 0.25                 # Idle send interval (seconds)
```

You should see:
```
Leader arm connected
Connected to MQTT broker
Leader sender started at 24 FPS
Move the leader arm to control the follower
```

The leader publishes servo positions to `watchman_robotarm/so-101/leader` for the frontend and follower.

### 3. Start Follower Controller (on Pi)
```bash
python scripts/follower.py
```
The command above uses `__init__` defaults.

Optional command-line parameters (with comments):
```bash
python scripts/follower.py \
  --follower-port /dev/ttyACM0 \             # Serial port for the follower arm
  --follower-id so_follower \                # Calibration ID for the follower arm
  --mqtt-broker-ip <MQTT_BROKER_IP> \        # MQTT broker IP or hostname
  --mqtt-broker-port 1883 \                  # MQTT broker port
  --mqtt-topic watchman_robotarm/so-101 \    # MQTT topic
  --max-relative-target 20 \                 # Safety clamp (max relative motion per step)
  --control-fps 24 \                         # Control loop frequency (Hz)
  --idle-send-interval 0.25                  # Idle send interval (seconds)
```

If you want to stream the follower camera over UDP (RTP/H.264), add:
```bash
  --camera /dev/video0 \                     # V4L2 device
  --cam-res 640x480 \                        # e.g. 320×240
  --video-host <rtp_to_rtsp_streamer.py ip>  # Defaults to --mqtt-broker-ip
```

You should see:
```
Connecting to follower arm on /dev/ttyACM0...
Follower arm connected
Connected to MQTT broker at <MQTT_BROKER_IP>:1883
Subscribed to topic: watchman_robotarm/so-101/leader
```

The follower publishes servo positions to `watchman_robotarm/so-101/follower` for the frontend.

### 4. (Optional) RTP to RTSP video Streamer

If `scripts/follower.py` is given a camera device when executed, it will stream RTP/H.264 over UDP to `--video-host` on UDP port `5000`.

The script `scripts/rtp_to_rtsp_streamer.py` listens for that RTP stream and re-publishes it as RTSP so multiple clients can view it.

1) On the Pi (follower), start the follower with camera streaming enabled and set `--video-host` to the machine that will run the RTSP server (often your PC):
```bash
python scripts/follower.py \
  --camera /dev/video0 \
  --cam-res 640x480 \
  --video-host <PC_IP>
```

2) On the PC (or any host on the same network), run the RTSP server:
```bash
python scripts/rtp_to_rtsp_streamer.py
```

Optional command-line parameters (with comments):
```bash
python scripts/rtp_to_rtsp_streamer.py \
  --udp-port 5000 \          # UDP port to listen for incoming RTP/H.264
  --rtsp-host 0.0.0.0 \      # Bind address for the RTSP server
  --rtsp-port 8000 \         # RTSP server port
  --mount-point /camera \    # RTSP path
  --jitter-ms 50              # Jitterbuffer latency (ms)
```

You should see a log like:
```
Starting RTSP server at rtsp://0.0.0.0:8000/camera (UDP in: 5000)
```

3) View the stream from Watchman:
- `rtsp://<PC_IP>:8000/camera`


### 5. Web / Watchman

I have made a new profile within the Watchman users that has the video wall all set up.
In profiles go to SO-101 Digital Twin.
Scene 0 is probably all you will need but just in case on Scene 1 I have made some example panels showcasing how you can use the HTML cam control parameters to "lock" the camera to potentially useful views.

Open index.html from the same IP as your front-end nginx server:
```
http://<IP_ADDR>/index.html
```
The page connects to a JSON-RPC-over-WebSocket endpoint at `ws://<IP_ADDR>:9000`.

Optional URL parameters
```
index.html?#leader=0&followerColor=0xff69b4 # Pink follower arm only

# Supported params:
?#model=so-101               # Model name (accepts so-101 or so-101.glb)
&follower=1                  # Show follower model (0 or 1)
&leader=1                    # Show leader model (0 or 1)
&followerColor=0x88ccff      # 0xRRGGBB or #RRGGBB
&leaderColor=0xffffff        # 0xRRGGBB or #RRGGBB
&followerOpacity=0.8         # 0.0 - 1.0
&leaderOpacity=1.0           # 0.0 - 1.0
&camX=0&camY=0&camZ=0        # Camera position
&camTargetX=0&camTargetY=0&camTargetZ=0  # Camera target
&wireframe=0                 # Render mode (0 = ghost, 1 = wireframe)
&debug=0                     # Show console logs overlay (0 or 1)
```
You should see:
Robot arm model updating to match leader/follower joint data

### 6. Control!

Hopefully now if set up correctly you should be able to see the digital twins of both follower and leader react when leader is controlled.


