#!/usr/bin/env python3
"""
MQTT-controlled SO-ARM101 Follower with Jump Protection
Run this on the Raspberry Pi connected to the follower arm
"""

import json
import logging
import argparse
import time
import sys
from pathlib import Path
import threading
from datetime import datetime

import paho.mqtt.client as mqtt  # type: ignore[import-not-found]

# Add parent directory to path to import lerobot
sys.path.insert(0, str(Path(__file__).parent.parent))

from lerobot.robots.so_follower import SO101Follower, SO101FollowerConfig

# Basic Logging set up
logging.basicConfig(level=logging.INFO,
                    format= "%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

class Follower:
    """
    SO-ARM101 Follower controlled via MQTT messages from leader.py
    Run this on the Raspberry Pi connected to the follower arm
    """

    def __init__(
        self,
        follower_port: str = "/dev/ttyACM0",
        follower_id: str = "so_follower",
        mqtt_broker_ip: str = "192.168.1.107",
        mqtt_broker_port: int = 1883,
        mqtt_topic: str = "watchman_robotarm/so-101",
        max_relative_target: float = 20.0,
        control_fps: int = 30,
        idle_send_interval: float = 0.25,
        follower_feedback: bool = True,
    ):
        # Initialize follower
        follower_config = SO101FollowerConfig(
            port=follower_port,
            id=follower_id,
            max_relative_target=max_relative_target,
            use_degrees=True,
        )
        self.follower = SO101Follower(follower_config)
        self.max_relative_target = max_relative_target
        self.control_fps = max(1, int(control_fps))

        # MQTT config
        self.mqtt_broker_ip = mqtt_broker_ip
        self.mqtt_broker_port = int(mqtt_broker_port)
        self.mqtt_topic = mqtt_topic
        self.follower_feedback = follower_feedback  # Whether to publish current servo positions for frontend display
        self.idle_send_interval = max(0.0, float(idle_send_interval))

        self._mqtt_client: mqtt.Client | None = None
        self._mqtt_connected = threading.Event()

        self.is_running = False

        self.last_send_time = 0.0

        self.goal_pos = {}
        self._goal_pos_lock = threading.Lock()
        self._last_cmd_timestamp: datetime | None = None
        self._bus_lock = threading.Lock()

    def _publish_actual_joint_angles(self, present_pos: dict) -> None:
        if not self._mqtt_client or not self._mqtt_connected.is_set():
            return

        message = {
            "method": "set_actual_joint_angles",
            "params": {"joints": {f"{k}.pos": v for k, v in present_pos.items()}},
            "timestamp": time.time(),
        }
        self._mqtt_client.publish(self.mqtt_topic+"/follower", json.dumps(message), retain=False)

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            logger.info(
                "Connected to MQTT broker at %s:%s",
                self.mqtt_broker_ip,
                self.mqtt_broker_port,
            )
            self._mqtt_connected.set()
            client.subscribe(self.mqtt_topic+"/leader")
            logger.info("Subscribed to topic: %s", self.mqtt_topic+"/leader")
        else:
            logger.error("Failed to connect to MQTT broker. Return code: %s", rc)
            self._mqtt_connected.clear()

    def _on_disconnect(self, client, userdata, rc):
        self._mqtt_connected.clear()
        logger.warning("Disconnected from MQTT broker. Return code: %s", rc)

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except Exception:
            logger.warning("Invalid MQTT JSON payload on %s", msg.topic)
            return

        method = payload.get("method")
        if method != "set_follower_joint_angles":
            return

        # Ignore out-of-order commands if timestamps are present
        try:
            ts = payload.get("timestamp")
            cmd_ts = datetime.fromisoformat(ts) if ts else None
        except Exception:
            cmd_ts = None

        with self._goal_pos_lock:
            if cmd_ts is not None and self._last_cmd_timestamp is not None and cmd_ts <= self._last_cmd_timestamp:
                return
            self.set_goal_position(payload)
            if cmd_ts is not None:
                self._last_cmd_timestamp = cmd_ts

    def start(self, stop_event=None):
        try:
            # Connect to follower arm
            logger.info(f"Connecting to follower arm on {self.follower.config.port}...")
            self.follower.connect()
            logger.info("Follower arm connected")

            # Connect to MQTT broker
            self._mqtt_client = mqtt.Client()
            self._mqtt_client.on_connect = self._on_connect
            self._mqtt_client.on_disconnect = self._on_disconnect
            self._mqtt_client.on_message = self._on_message

            logger.info(
                "Connecting to MQTT broker at %s:%s...",
                self.mqtt_broker_ip,
                self.mqtt_broker_port,
            )
            self._mqtt_client.connect(self.mqtt_broker_ip, self.mqtt_broker_port, keepalive=60)
            self._mqtt_client.loop_start()

            if not self._mqtt_connected.wait(timeout=5):
                logger.error("Failed to connect to MQTT broker within timeout")
                return

            # Send initial servo positions for frontend display (if enabled)
            try:
                with self._bus_lock:
                    present_pos = self.follower.bus.sync_read("Present_Position")
            except Exception as e:
                logger.error("Failed to sync read 'Present_Position': %s", e)
                present_pos = None
            if self.follower_feedback and present_pos is not None:
                try:
                    self._publish_actual_joint_angles(present_pos)
                except Exception as e:
                    logger.warning("Failed to publish present_pos over MQTT: %s", e)

            self.last_send_time = time.perf_counter()

            # Start set_joints loop in separate thread to continuously update follower arm position
            logger.debug("Starting set_joints thread...")
            set_joints_thread = threading.Thread(target=self.set_joints, args=(stop_event,))
            set_joints_thread.start()
            logger.debug("set_joints thread started")

            while stop_event is None or not stop_event.is_set():
                now = time.perf_counter()

                # Periodic publish (keepalive) for frontend display
                if self.follower_feedback and (now - self.last_send_time) >= self.idle_send_interval:
                    try:
                        with self._bus_lock:
                            present_pos = self.follower.bus.sync_read("Present_Position")
                    except Exception as e:
                        logger.error("Failed to sync read 'Present_Position': %s", e)
                        present_pos = None
                    if present_pos is not None:
                        try:
                            self._publish_actual_joint_angles(present_pos)
                        except Exception as e:
                            logger.warning("Failed to publish present_pos over MQTT: %s", e)
                        self.last_send_time = now

                time.sleep(0.01)
        except Exception as e:
            logger.exception("Error in follower loop: %s", e)
        finally:
            try:
                if self._mqtt_client is not None:
                    self._mqtt_client.loop_stop()
                    self._mqtt_client.disconnect()
            except Exception:
                pass

    def set_goal_position(self, payload):
        # Extract joint angles from payload
        joints = payload.get("params", {}).get("joints", {})

        # Extract leader arm positions (remove .pos suffix)
        self.goal_pos = {key.removesuffix(".pos"): val for key, val in joints.items() if key.endswith(".pos")}

    def set_joints(self, stop_event=None):
        loop_time = 1.0 / float(self.control_fps)
        while stop_event is None or not stop_event.is_set():
            loop_start = time.perf_counter()
            with self._goal_pos_lock:
                goal_pos = dict(self.goal_pos)

            if not goal_pos:
                time.sleep(min(0.05, loop_time))
                continue

            action = {f"{motor}.pos": val for motor, val in goal_pos.items()}
            try:
                with self._bus_lock:
                    self.follower.send_action(action)
            except Exception as e:
                # Don't crash the thread if the bus glitches; just back off and retry.
                logger.warning("send_action failed (will retry): %s", e)
                time.sleep(0.1)

            # Sleep to maintain control rate.
            elapsed = time.perf_counter() - loop_start
            sleep_time = max(0.0, loop_time - elapsed)
            time.sleep(sleep_time)


class CameraStreamer:
    """
    Optional GStreamer-based camera streamer that captures video from a V4L2 device,
    encodes it as H.264, and sends it via UDP for frontend display.
    """

    def __init__(self, camera_device: str, camera_resolution: str, video_host: str = "192.168.1.107", follower_camera_port: int = 5000):
        self.camera_device = camera_device
        self.camera_resolution = camera_resolution
        self.video_host = video_host
        self.follower_camera_port = follower_camera_port

    def start(self, stop_event=None):
        import subprocess
        width, height = self.camera_resolution.split('x')
        gst_cmd = [
            'gst-launch-1.0',
            'v4l2src', f'device={self.camera_device}',
            '!', f'video/x-raw,width={width},height={height},framerate=30/1',
            '!', 'videoconvert',
            '!', 'video/x-raw,format=I420',          # <--- force 4:2:0
            '!', 'x264enc',
                'tune=zerolatency',
                'speed-preset=ultrafast',
                'bitrate=500',
                'key-int-max=30',                    # <--- recover after loss
                'bframes=0',
                'byte-stream=true',
            '!', 'rtph264pay', 'pt=96', 'config-interval=1', 'mtu=1200',
            '!', 'udpsink', f'host={self.video_host}', f'port={self.follower_camera_port}',
                'sync=false', 'async=false'
        ]

        # Loop to continuously run the GStreamer pipeline, restarting if it crashes, until stop_event is set
        while stop_event is None or not stop_event.is_set():
            t = time.localtime()
            timestamp = f"{t.tm_hour}:{t.tm_min:02}:{t.tm_sec:02}.{int(time.time()%1*10):01}"
            logger.info(f"[{timestamp}] Starting GStreamer H.264 pipeline: {' '.join(gst_cmd)}")
            try:
                proc = subprocess.Popen(gst_cmd)
                while True:
                    if stop_event is not None and stop_event.is_set():
                        logger.info("Stop event set, terminating camera pipeline...")
                        proc.terminate()
                        proc.wait()
                        return
                    ret = proc.poll()
                    if ret is not None:
                        logger.warning(f"Camera pipeline exited with code {ret}. Restarting in 2s...")
                        proc.wait()
                        time.sleep(2)
                        break
                    time.sleep(0.2)
            except Exception as e:
                logger.error(f"Error starting GStreamer pipeline: {e}. Retrying in 2s...")
                time.sleep(2)

def parse_args():
    p = argparse.ArgumentParser(description="SO-ARM101 follower")
    p.add_argument("--follower-port", default="/dev/ttyACM0")
    p.add_argument("--follower-id", default="so_follower")
    p.add_argument("--mqtt-broker-ip", default="192.168.1.107")
    p.add_argument("--mqtt-broker-port", type=int, default=1883)
    p.add_argument("--mqtt-topic", default="watchman_robotarm/so-101")
    p.add_argument("--max-relative-target", type=float, default=20.0)
    p.add_argument("--control-fps", type=int, default=30)
    p.add_argument("--idle-send-interval", type=float, default=0.25)

    # Optional camera
    p.add_argument("--camera", dest="camera_device", default=None,
                   help="Enable ustreamer and use this V4L2 device, e.g. /dev/video0")
    p.add_argument("--cam-res", dest="camera_resolution", default="640x480")
    p.add_argument("--video-host",dest="video_host",default=None,
        help="Host to send UDP video stream to (defaults to --mqtt-broker-ip)",
    )
    return p.parse_args()

def main():
    args = parse_args()

    follower = Follower(
        follower_port=args.follower_port,
        follower_id=args.follower_id,
        mqtt_broker_ip=args.mqtt_broker_ip,
        mqtt_broker_port=args.mqtt_broker_port,
        mqtt_topic=args.mqtt_topic,
        max_relative_target=args.max_relative_target,
        control_fps=args.control_fps,
        idle_send_interval=args.idle_send_interval,
    )

    stop_event = threading.Event()
    threads = []

    follower_thread = threading.Thread(target=follower.start, args=(stop_event,))
    follower_thread.start()
    threads.append(follower_thread)

    # Start camera streamer if camera device is provided
    if args.camera_device:
        camera_streamer = CameraStreamer(
            camera_device=args.camera_device,
            camera_resolution=args.camera_resolution,
            video_host=(args.video_host or args.mqtt_broker_ip),
            follower_camera_port=5000,
        )
        camera_streamer_thread = threading.Thread(
            target=camera_streamer.start, args=(stop_event,)
        )
        camera_streamer_thread.start()
        threads.append(camera_streamer_thread)

    try:
        while any(t.is_alive() for t in threads):
            for t in threads:
                t.join(timeout=0.2)
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received. Shutting down threads...")
        stop_event.set()
        for t in threads:
            t.join()

if __name__ == "__main__":
    main()