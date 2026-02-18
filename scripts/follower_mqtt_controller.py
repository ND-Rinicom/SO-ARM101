#!/usr/bin/env python3
"""
MQTT-controlled SO-ARM101 Follower with Jump Protection
Optional ustreamer camera server.
Run this on the Raspberry Pi connected to the follower arm
"""

import argparse
import json
import logging
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Add parent directory to path to import lerobot
sys.path.insert(0, str(Path(__file__).parent.parent))

import paho.mqtt.client as mqtt
from lerobot.robots.so_follower import SO101Follower, SO101FollowerConfig
from lerobot.robots.robot import ensure_safe_goal_position

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def start_ustreamer(
    device: str,
    host: str = "0.0.0.0",
    port: int = 8080,
    resolution: str = "640x480",
) -> subprocess.Popen:
    """
    Start ustreamer as a background process.
    Returns a Popen handle so we can terminate it on exit.
    """
    if shutil.which("ustreamer") is None:
        raise RuntimeError(
            "ustreamer not found. Install it with: sudo apt install -y ustreamer"
        )

    cmd = [
        "ustreamer",
        f"--device={device}",
        f"--host={host}",
        f"--port={port}",
        f"--resolution={resolution}",
    ]

    logger.info("Starting ustreamer: %s", " ".join(cmd))
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )

    time.sleep(0.4)
    if proc.poll() is not None:
        out = ""
        try:
            out = (proc.stdout.read() or "").strip() if proc.stdout else ""
        except Exception:
            pass
        raise RuntimeError(f"ustreamer exited immediately.\nOutput:\n{out}")
    logger.info("ustreamer started (pid=%s). Stream: http://<pi-ip>:%d/stream", proc.pid, port)
    return proc


def start_http_server(
    port: int = 8001,
    directory: str = "~",
) -> subprocess.Popen:
    """
    Start Python HTTP server as a background process.
    Returns a Popen handle so we can terminate it on exit.
    """
    directory = Path(directory).expanduser()
    
    cmd = [
        "python3",
        "-m",
        "http.server",
        str(port),
        "--directory",
        str(directory),
    ]

    logger.info("Starting HTTP server: %s", " ".join(cmd))
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )

    time.sleep(0.2)
    if proc.poll() is not None:
        out = ""
        try:
            out = (proc.stdout.read() or "").strip() if proc.stdout else ""
        except Exception:
            pass
        raise RuntimeError(f"HTTP server exited immediately.\nOutput:\n{out}")
    logger.info("HTTP server started (pid=%s). Serving: http://<pi-ip>:%d/", proc.pid, port)
    return proc


def stop_process(proc: Optional[subprocess.Popen], name: str) -> None:
    if not proc:
        return
    try:
        if proc.poll() is None:
            logger.info("Stopping %s (pid=%s)...", name, proc.pid)
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
    except Exception as e:
        logger.warning("Failed stopping %s: %s", name, e)


class FollowerSafetyController:
    """
    MQTT-based follower controller with local jump protection.
    Optionally starts ustreamer for a webcam and HTTP server for file serving.
    """

    def __init__(
        self,
        follower_port: str = "/dev/ttyACM0",
        follower_id: str = "so_follower",
        mqtt_broker: str = "192.168.1.107",
        mqtt_port: int = 1883,
        mqtt_topic: str = "watchman_robotarm/so-101",
        max_relative_target: float = 20.0,
        use_degrees: bool = True,
        camera_device: Optional[str] = None,
        camera_host: str = "0.0.0.0",
        camera_port: int = 8080,
        camera_resolution: str = "640x480",
        http_server_port: Optional[int] = None,
        http_server_dir: str = "~",
    ):
        # Initialize follower
        follower_config = SO101FollowerConfig(
            port=follower_port,
            id=follower_id,
            max_relative_target=max_relative_target,
            use_degrees=use_degrees,
        )
        self.follower = SO101Follower(follower_config)
        self.max_relative_target = max_relative_target

        # Camera config
        self.camera_device = camera_device
        self.camera_host = camera_host
        self.camera_port = camera_port
        self.camera_resolution = camera_resolution
        self._camera_proc: Optional[subprocess.Popen] = None

        # HTTP server config
        self.http_server_port = http_server_port
        self.http_server_dir = http_server_dir
        self._http_server_proc: Optional[subprocess.Popen] = None

        # Initialize MQTT
        self.mqtt_client = mqtt.Client()
        self.mqtt_client.on_connect = self._on_connect
        self.mqtt_client.on_message = self._on_message
        self.mqtt_client.on_disconnect = self._on_disconnect
        self.mqtt_broker = mqtt_broker
        self.mqtt_port = mqtt_port
        self.mqtt_topic = mqtt_topic

        # Threading for position publishing
        self.position_publish_interval = 0.1  # Publish every 100ms
        self.publish_thread = None
        self.publish_thread_stop = threading.Event()

        self.is_running = False

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            logger.info(f"Connected to MQTT broker at {self.mqtt_broker}:{self.mqtt_port}")
            client.subscribe(self.mqtt_topic)
            logger.info(f"Subscribed to topic: {self.mqtt_topic}")
        else:
            logger.error(f"Failed to connect to MQTT broker. Return code: {rc}")

    def _on_disconnect(self, client, userdata, rc):
        logger.warning(f"Disconnected from MQTT broker. Return code: {rc}")
        if rc != 0:
            logger.info("Attempting to reconnect...")

    def _publish_follower_positions(self):
        """Publish current follower joint angles to MQTT"""
        try:
            present_pos = self.follower.bus.sync_read("Present_Position")
            
            joints = {
                "shoulder_pan.pos": present_pos.get("shoulder_pan", 0),
                "shoulder_lift.pos": present_pos.get("shoulder_lift", 0),
                "elbow_flex.pos": present_pos.get("elbow_flex", 0),
                "wrist_flex.pos": present_pos.get("wrist_flex", 0),
                "wrist_roll.pos": present_pos.get("wrist_roll", 0),
                "gripper.pos": present_pos.get("gripper", 0),
            }
            
            message = {
                "jsonrpc": "2.0",
                "id": f"follower-{time.time()}",
                "method": "set_follower_joint_angles",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "params": {
                    "units": "degrees",
                    "joints": joints
                }
            }
            
            self.mqtt_client.publish(self.mqtt_topic, json.dumps(message))
            
        except Exception as e:
            logger.error(f"Error publishing follower positions: {e}")

    def _position_publish_loop(self):
        """Background thread that periodically publishes follower positions"""
        logger.info(f"Position publishing thread started (interval: {self.position_publish_interval}s)")
        while not self.publish_thread_stop.is_set():
            try:
                self._publish_follower_positions()
            except Exception as e:
                logger.error(f"Error in position publish loop: {e}")
            
            # Wait for the interval or until stop is signaled
            self.publish_thread_stop.wait(self.position_publish_interval)
        
        logger.info("Position publishing thread stopped")

    def _on_message(self, client, userdata, msg):
        try:
            message = json.loads(msg.payload.decode())

            method = message.get("method")
            if method != "set_leader_joint_angles":
                # Follower only processes set_leader_joint_angles commands
                return

            params = message.get("params", {})
            joints = params.get("joints", {})

            goal_pos = {
                key.removesuffix(".pos"): val
                for key, val in joints.items()
                if key.endswith(".pos")
            }

            if not goal_pos:
                logger.warning("Received empty action, skipping")
                return

            present_pos = self.follower.bus.sync_read("Present_Position")

            if self.max_relative_target is not None:
                goal_present_pos = {key: (g_pos, present_pos[key]) for key, g_pos in goal_pos.items()}
                safe_goal_pos = ensure_safe_goal_position(goal_present_pos, self.max_relative_target)
            else:
                safe_goal_pos = goal_pos

            self.follower.bus.sync_write("Goal_Position", safe_goal_pos)

        except json.JSONDecodeError:
            logger.error(f"Failed to parse JSON: {msg.payload}")
        except Exception as e:
            logger.error(f"Error processing message: {e}")

    def start(self):
        try:
            # Optional camera
            if self.camera_device:
                self._camera_proc = start_ustreamer(
                    device=self.camera_device,
                    host=self.camera_host,
                    port=self.camera_port,
                    resolution=self.camera_resolution,
                )

            # Optional HTTP server
            if self.http_server_port:
                self._http_server_proc = start_http_server(
                    port=self.http_server_port,
                    directory=self.http_server_dir,
                )

            # Connect to follower arm
            logger.info(f"Connecting to follower arm on {self.follower.config.port}...")
            self.follower.connect()
            logger.info("Follower arm connected")

            # Connect to MQTT broker
            logger.info(f"Connecting to MQTT broker at {self.mqtt_broker}:{self.mqtt_port}...")
            self.mqtt_client.connect(self.mqtt_broker, self.mqtt_port, keepalive=60)

            # Start background thread for position publishing
            self.publish_thread_stop.clear()
            self.publish_thread = threading.Thread(target=self._position_publish_loop, daemon=True)
            self.publish_thread.start()

            logger.info("Follower controller started. Waiting for targets...")
            logger.info(f"Jump protection: max_relative_target = {self.max_relative_target}")
            self.is_running = True
            self.mqtt_client.loop_forever()

        except KeyboardInterrupt:
            logger.info("\nKeyboard interrupt received")
            self.stop()
        except Exception as e:
            logger.error(f"Error starting controller: {e}")
            self.stop()

    def stop(self):
        if self.is_running:
            logger.info("Stopping follower controller...")
            
            # Stop position publishing thread
            if self.publish_thread and self.publish_thread.is_alive():
                self.publish_thread_stop.set()
                self.publish_thread.join(timeout=2)
            
            try:
                self.mqtt_client.loop_stop()
            except Exception:
                pass
            try:
                self.mqtt_client.disconnect()
            except Exception:
                pass
            try:
                self.follower.disconnect()
            except Exception:
                pass
            self.is_running = False

        stop_process(self._camera_proc, "ustreamer")
        stop_process(self._http_server_proc, "HTTP server")
        logger.info("Follower controller stopped")


def parse_args():
    p = argparse.ArgumentParser(description="SO-ARM101 follower (MQTT) with optional ustreamer camera and HTTP server.")
    p.add_argument("--follower-port", default="/dev/ttyACM0")
    p.add_argument("--follower-id", default="so_follower")
    p.add_argument("--mqtt-broker", default="192.168.1.107")
    p.add_argument("--mqtt-port", type=int, default=1883)
    p.add_argument("--mqtt-topic", default="watchman_robotarm/so-101")
    p.add_argument("--max-relative-target", type=float, default=20.0)
    p.add_argument("--use-degrees", action="store_true", default=True)

    # Optional camera
    p.add_argument("--camera", dest="camera_device", default=None,
                   help="Enable ustreamer and use this V4L2 device, e.g. /dev/video0")
    p.add_argument("--cam-host", default="0.0.0.0")
    p.add_argument("--cam-port", type=int, default=8080)
    p.add_argument("--cam-res", dest="camera_resolution", default="640x480")

    # Optional HTTP server
    p.add_argument("--http-server", dest="http_server_port", type=int, default=None,
                   help="Enable Python HTTP server on this port, e.g. 8001")
    p.add_argument("--http-dir", dest="http_server_dir", default="~",
                   help="Directory to serve via HTTP server (default: ~)")
    return p.parse_args()


def main():
    args = parse_args()

    controller = FollowerSafetyController(
        follower_port=args.follower_port,
        follower_id=args.follower_id,
        mqtt_broker=args.mqtt_broker,
        mqtt_port=args.mqtt_port,
        mqtt_topic=args.mqtt_topic,
        max_relative_target=args.max_relative_target,
        use_degrees=args.use_degrees,
        camera_device=args.camera_device,
        camera_host=args.cam_host,
        camera_port=args.cam_port,
        camera_resolution=args.camera_resolution,
        http_server_port=args.http_server_port,
        http_server_dir=args.http_server_dir,
    )

    controller.start()


if __name__ == "__main__":
    main()
