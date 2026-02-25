#!/usr/bin/env python3
"""
UDP-controlled SO-ARM101 Follower with Jump Protection
Run this on the Raspberry Pi connected to the follower arm
"""

import json
import socket
import logging
import argparse
import time
import sys
from pathlib import Path
import threading

# Add parent directory to path to import lerobot
sys.path.insert(0, str(Path(__file__).parent.parent))

from lerobot.robots.so_follower import SO101Follower, SO101FollowerConfig
from lerobot.robots.robot import ensure_safe_goal_position

# Basic Logging set up
logging.basicConfig(level=logging.INFO,
                    format= "%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

class Follower:
    def __init__(
        self,
        follower_port: str = "/dev/ttyACM0",
        follower_id: str = "so_follower",
        bridge_ip: str = "192.168.1.107",
        bridge_port: int = 9000,
        max_relative_target: float = 20.0,
        use_degrees: bool = True,
        follower_feedback: bool = True,
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

        # UDP bridge config
        self.follower_sock = None
        self.bridge_ip = bridge_ip
        self.bridge_port = bridge_port
        self.follower_feedback = follower_feedback # Whether to send current servo positions back to bridge for frontend display

        self.is_running = False

    def _send_servo_udp(self, present_pos: dict) -> None:
        payload = json.dumps(
            {
                "method": "servo_positions",
                "timestamp": time.time(),
                "joints": {f"{k}.pos": v for k, v in present_pos.items()},
            }
        ).encode("utf-8")
        self.follower_sock.sendto(payload, (self.bridge_ip, self.bridge_port))

    def start(self, stop_event=None):
        try:
            # Connect to follower arm
            logger.info(f"Connecting to follower arm on {self.follower.config.port}...")
            self.follower.connect()
            logger.info("Follower arm connected")

            # Create UDP follower_sock socket to receive instructions 
            # and send servo positions to/from bridge
            self.follower_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.follower_sock.bind(('0.0.0.0', self.bridge_port))
            self.follower_sock.settimeout(0)

            # Send initial servo positions to bridge for frontend display (if enabled)
            present_pos = self.follower.bus.sync_read("Present_Position")
            if self.follower_feedback:
                try:
                    self._send_servo_udp(present_pos)
                    logger.debug(f"Sent present_pos to bridge: {present_pos}")
                except Exception as e:
                    logger.warning("Failed to send present_pos over UDP: %s", e)

            while stop_event is None or not stop_event.is_set():
                try:
                    # Receive instructions from bridge via UDP
                    data, addr = self.follower_sock.recvfrom(1024) # Recive 1024 bytes from bridge
                    payload = json.loads(data.decode("utf-8"))
                    method = payload.get("method")
                    logger.debug(f"Received UDP message with method: {method} from bridge {addr}")
                    if method == "set_follower_joint_angles":
                        self.set_joints(payload)
                except socket.error:
                    # No data received, continue waiting
                    continue
                except Exception:
                    logger.warning("Invalid UDP JSON payload from bridge %s", addr)
        except Exception as e:
            logger.exception(f"Error in UDP socket: {e}")

    def set_joints(self, payload):
        # Extract joint angles from payload
        joints = payload.get("params", {}).get("joints", {})

        # Extract leader arm positions (remove .pos suffix)
        goal_pos = {
            key.removesuffix(".pos"): val
            for key, val in joints.items()
            if key.endswith(".pos")
        }

        # Read current servo positions
        present_pos = self.follower.bus.sync_read("Present_Position")

        # Send current positions to bridge for frontend display (if enabled)
        if self.follower_feedback:
            try:
                self._send_servo_udp(present_pos)
                logger.debug(f"Sent present_pos to bridge: {present_pos}")
            except Exception as e:
                logger.warning("Failed to send present_pos over UDP: %s", e)
        
        # Ensure the goal position is within the max_relative_target of the current position
        if self.max_relative_target is not None:
            goal_present_pos = {key: (g_pos, present_pos[key]) for key, g_pos in goal_pos.items()}
            safe_goal_pos = ensure_safe_goal_position(goal_present_pos, self.max_relative_target)
        else:
            safe_goal_pos = goal_pos

        # Send safe goal position to follower
        self.follower.bus.sync_write("Goal_Position", safe_goal_pos)

class CameraStreamer:
    def __init__(self, camera_device: str, camera_resolution: str, bridge_ip: str = "192.168.1.107", follower_camera_port: int = 5000):
        self.camera_device = camera_device
        self.camera_resolution = camera_resolution
        self.bridge_ip = bridge_ip
        self.follower_camera_port = follower_camera_port

    def start(self, stop_event=None):
        import subprocess
        width, height = self.camera_resolution.split('x')
        gst_cmd = [
            'gst-launch-1.0',
            'v4l2src', f'device={self.camera_device}',
            '!', f'video/x-raw,width={width},height={height},framerate=30/1',
            '!', 'videoconvert',
            '!', 'x264enc', 'tune=zerolatency', 'bitrate=500', 'speed-preset=ultrafast',
            '!', 'rtph264pay', 'pt=96', 'config-interval=1',
            '!', 'udpsink', f'host={self.bridge_ip}', f'port={self.follower_camera_port}', 'sync=false', 'async=false'
        ]
        logger.info(f"Starting GStreamer H.264 pipeline: {' '.join(gst_cmd)}")
        try:
            proc = subprocess.Popen(gst_cmd)
            if stop_event is not None:
                while not stop_event.is_set():
                    time.time.sleep(0.2)
                proc.terminate()
                proc.wait()
            else:
                proc.wait()
        except Exception as e:
            logger.error(f"Error starting GStreamer pipeline: {e}")


def parse_args():
    p = argparse.ArgumentParser(description="SO-ARM101 follower")
    p.add_argument("--follower-port", default="/dev/ttyACM0")
    p.add_argument("--follower-id", default="so_follower")
    p.add_argument("--bridge-ip", default="192.168.1.107")
    p.add_argument("--bridge-port", type=int, default=9000)
    p.add_argument("--max-relative-target", type=float, default=20.0)
    p.add_argument("--use-degrees", action="store_true", default=True)

    # Optional camera
    p.add_argument("--camera", dest="camera_device", default=None,
                   help="Enable ustreamer and use this V4L2 device, e.g. /dev/video0")
    p.add_argument("--cam-res", dest="camera_resolution", default="640x480")

    return p.parse_args()

def main():
    args = parse_args()

    follower = Follower(
        follower_port=args.follower_port,
        follower_id=args.follower_id,
        bridge_ip=args.bridge_ip,
        bridge_port=args.bridge_port,
        max_relative_target=args.max_relative_target,
        use_degrees=args.use_degrees,
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
            bridge_ip=args.bridge_ip,
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