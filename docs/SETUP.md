# Setup

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

### 7. Setup Web
To host the front end Web page I used Nginx (https://nginx.org/)
You probably know how to set up and host a web page yourself but here is a full set up guide anyways:

1) Install and enable nginx 

```bash
sudo dnf install -y nginx
sudo systemctl enable --now nginx
sudo firewall-cmd --permanent --add-service=http
sudo firewall-cmd --reload
```

2) Copy project files to nginx web directory

```bash
sudo mkdir -p /var/www/so-101
sudo cp -r /<path to this directory>/front-end/* /var/www/so-101/
sudo chown -R nginx:nginx /var/www/so-101
sudo chmod -R 755 /var/www/so-101
```

3) Set up nginx config 

```bash
sudo nano /etc/nginx/conf.d/so-101.conf
```

```
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name so-101-server;

    root /var/www/so-101;
    index index.html;

    location / {
        try_files $uri $uri/ =404;
    }
}
```

4) Reload nginx and test

    ```
    sudo nginx -t
    sudo systemctl reload nginx
    ```

### 8. Visual Fusion+ (Watchman) Video Wall (optional)

This project is inteneded to be used on Visual Fusion+ (Watchman) Video Wall.
To set this up you will need a device with Watchman installed and set up that is connected to internet and at least one monitor display.

Within the device cmd find and remember the IP
```bash
ip a
```

Then start Watchman (Wherever you watchman is located)
```bash
workspace/watchman/build/opt/watchman/watchman
```

When watchman is running, on a seperate device connect via any browser. 
http://<Watchman Device IP>

Enter the password
Then if its still there you can switch your profile in the top left to "Robot Arm"
Which has an example set up on scene 0.

Otherwise create a stream of your web front end via the correct URL, and add any optional URL prameters you like. See Usage.md

e.g. http://0.0.0.0/index.html?#follower=0&leaderColor=0xFF6984 for a pink leader digital twin.


### 9. Set UDP priorities (optional)

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
python scripts/follower_udp_controller.py \
  --follower-port /dev/ttyACM0 \            # Serial port for the follower arm
  --follower-id so_follower \               # Calibration ID for the follower arm
  --mqtt-broker 0.0.0.0 \                   # MQTT broker IP or hostname
  --mqtt-port 1883 \                        # MQTT broker port
  --mqtt-topic watchman_robotarm/so-101 \   # MQTT topic for joint commands
  --max-relative-target 20.0 \              # Max per-step joint change
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


