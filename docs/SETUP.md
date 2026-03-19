# Setup

### 1. Create Virtual Environment (Recommended)
On both PC and Raspberry Pi create and activate venv:
```bash
python3 -m venv ./lerobot-venv
source ./lerobot-venv/bin/activate
```

### 2. Install Dependencies

#### Both PC & Pi (with venv activated)
```bash
pip install paho-mqtt pyserial numpy feetech-servo-sdk
```

#### Pi (Follower PC) dependencies
Debian/Ubuntu/Raspberry Pi OS:
```bash
sudo apt update
sudo apt install -y \
    gstreamer1.0-tools \
    gstreamer1.0-plugins-base \
    gstreamer1.0-plugins-good \
    gstreamer1.0-plugins-bad \
    gstreamer1.0-plugins-ugly \
    gstreamer1.0-libav
```

Fedora:
```bash
sudo dnf install -y \
    gstreamer1 \
    gstreamer1-plugins-base \
    gstreamer1-plugins-good \
    gstreamer1-plugins-bad-free \
    gstreamer1-plugins-ugly \
    gstreamer1-libav
```

#### PC dependencies

System packages (required for `scripts/rtp_to_rtsp_streamer.py`):

Debian/Ubuntu:
```bash
sudo apt update
sudo apt install -y \
    python3-gi \
    gir1.2-gstreamer-1.0 \
    gir1.2-gst-plugins-base-1.0 \
    gir1.2-gst-rtsp-server-1.0 \
    gstreamer1.0-tools \
    gstreamer1.0-plugins-base \
    gstreamer1.0-plugins-good \
    gstreamer1.0-plugins-bad \
    gstreamer1.0-plugins-ugly \
    gstreamer1.0-libav
```

Fedora:
```bash
sudo dnf install -y \
    python3-gobject \
    gstreamer1-rtsp-server \
    gstreamer1 \
    gstreamer1-plugins-base \
    gstreamer1-plugins-good \
    gstreamer1-plugins-bad-free \
    gstreamer1-plugins-ugly \
    gstreamer1-libav
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
You need calibration files for both leader and follower. Calibration files are stored within `/lerobot/calibrations`.

There should already be calibrations set up in this directory however if there isn't you can set your own buy running the following on their respective machines:

NOTE: make sure you start the calibration with the arm at its middle points for all joints including wrist roll. Like so ![Follower arm calibtaion start position](img/SO-101-Calibration-Start-Position.png)

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


### 9. Set Port priorities (optional)

If wanting communication from the laptop (leader) to pi (follower) via UDP its a good idea to set the servo comunications as higher priority than the video stream as video packets droping is much better.

Bellow is some tc rules to prioritize the mqtt port over the rtp port. 
NOTE: the commands below are currenlty for wifi so switch "wlan0" to the appropriate network connection

On the pi cmd 
```bash
# Wipe existing rules
sudo tc qdisc del dev wlan0 root

# Rebuild with pfifo on robot band
sudo tc qdisc add dev wlan0 root handle 1: prio
sudo tc qdisc add dev wlan0 parent 1:1 handle 10: pfifo
sudo tc qdisc add dev wlan0 parent 1:2 handle 20: tbf rate 2mbit burst 32kbit latency 50ms

# Reapply filters
sudo tc filter add dev wlan0 protocol ip parent 1:0 prio 1 u32 \
    match ip dport 1883 0xffff flowid 1:1
sudo tc filter add dev wlan0 protocol ip parent 1:0 prio 2 u32 \
    match ip dport 5000 0xffff flowid 1:2
```