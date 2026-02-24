#!/usr/bin/env python3
"""
UDP-controlled SO-ARM101 Follower with Jump Protection
Run this on the Raspberry Pi connected to the follower arm
"""

import json
import socket
import logging
import argparse
from time import time
import sys
from pathlib import Path

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
        broker_ip: str = "192.168.1.107",
        broker_port: int = 9000,
        max_relative_target: float = 20.0,
        use_degrees: bool = True,
        camera_device: str = None,
        camera_resolution: str = "640x480",
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

        # UDP broker config
        self.follower_sock = None
        self.broker_ip = broker_ip
        self.broker_port = broker_port
        self.follower_feedback = follower_feedback # Whether to send current servo positions back to broker for frontend display

        # Camera config
        self.camera_device = camera_device
        self.camera_resolution = camera_resolution

        self.is_running = False

    def _send_servo_udp(self, present_pos: dict) -> None:
        payload = json.dumps(
            {
                "method": "servo_positions",
                "timestamp": time(),
                "joints": {f"{k}.pos": v for k, v in present_pos.items()},
            }
        ).encode("utf-8")
        self.follower_sock.sendto(payload, (self.broker_ip, self.broker_port))

    def start(self):
        try:
            # Connect to follower arm
            logger.info(f"Connecting to follower arm on {self.follower.config.port}...")
            self.follower.connect()
            logger.info("Follower arm connected")

            # Create UDP follower_sock socket to receive instructions 
            # and send servo positions to/from broker
            self.follower_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.follower_sock.bind(('0.0.0.0', self.broker_port))
            self.follower_sock.settimeout(1.0)

            while True:
                try:
                    # Receive instructions from broker (sent by MQTT-UDP bridge)
                    data, addr = self.follower_sock.recvfrom(1024) # Recive 1024 bytes from broker
                    payload = json.loads(data.decode("utf-8"))
                    method = payload.get("method")
                    logger.debug(f"Received UDP message with method: {method} from broker {addr}")
                    if method == "set_follower_joint_angles":
                        self.set_joints(payload)
                except socket.timeout:
                    # No data received send servo positions to broker for frontend display (if enabled)
                    logger.debug(f"No data received from broker for {self.follower_sock.gettimeout()} seconds")
                    if self.follower_feedback:
                        logger.debug("Sending current servo positions to broker for frontend display as self.follower_feedback is enabled")
                        try:
                            present_pos = self.follower.bus.sync_read("Present_Position")
                            self._send_servo_udp(present_pos)
                            logger.debug("Sent present_pos over UDP: %s", present_pos)
                        except Exception as e:
                            logger.warning("Failed to send present_pos over UDP: %s", e)
                except Exception:
                    logger.warning("Invalid UDP JSON payload from broker %s", addr)
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

        # Send current positions to broker for frontend display (if enabled)
        if self.follower_feedback:
            try:
                self._send_servo_udp(present_pos)
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

def parse_args():
    p = argparse.ArgumentParser(description="SO-ARM101 follower")
    p.add_argument("--follower-port", default="/dev/ttyACM0")
    p.add_argument("--follower-id", default="so_follower")
    p.add_argument("--broker-ip", default="192.168.1.107")
    p.add_argument("--broker-port", type=int, default=9000)
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
        broker_ip=args.broker_ip,
        broker_port=args.broker_port,
        max_relative_target=args.max_relative_target,
        use_degrees=args.use_degrees,
        camera_device=args.camera_device,
        camera_resolution=args.camera_resolution,
    )

    follower.start()

if __name__ == "__main__":
    main()