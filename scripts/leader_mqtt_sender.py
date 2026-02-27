#!/usr/bin/env python3
"""
MQTT Leader Sender for SO-ARM101
Run this on the PC connected to the leader arm
"""

import argparse
import json
import logging
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Add parent directory to path to import lerobot
sys.path.insert(0, str(Path(__file__).parent.parent))

import paho.mqtt.client as mqtt
from lerobot.teleoperators.so_leader import SO101Leader, SO101LeaderConfig

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class LeaderMQTTSender:
    """
    Reads positions from leader arm and sends them via MQTT to follower.
    Simple pass-through - all safety logic is on the follower side.
    """
    
    def __init__(
        self,
        leader_port: str = "/dev/ttyACM0",
        leader_id: str = "so_leader",
        mqtt_broker: str = "192.168.1.107",
        mqtt_port: int = 1883,
        mqtt_topic: str = "watchman_robotarm/so-101/leader",
        fps: int = 60,
        use_degrees: bool = True,
    ):
        """
        Args:
            leader_port: Serial port for leader arm (e.g., "/dev/ttyACM0")
            leader_id: ID for calibration file (e.g., "so_leader")
            mqtt_broker: MQTT broker address (e.g., "192.168.1.107")
            mqtt_port: MQTT broker port (default: 1883)
            mqtt_topic: MQTT topic to publish targets to
            fps: Target control loop frequency
            use_degrees: Use degrees (True) or normalized -100 to 100 range (False)
        """
        # Initialize leader
        leader_config = SO101LeaderConfig(
            port=leader_port,
            id=leader_id,
            use_degrees=use_degrees,
        )
        self.leader = SO101Leader(leader_config)
        
        # Initialize MQTT
        self.mqtt_client = mqtt.Client()
        self.mqtt_client.on_connect = self._on_connect
        self.mqtt_client.on_disconnect = self._on_disconnect
        self.mqtt_broker = mqtt_broker
        self.mqtt_port = mqtt_port
        self.mqtt_topic = mqtt_topic
        
        self.fps = fps
        self.is_running = False
        self.is_connected = False
        
    def _on_connect(self, client, userdata, flags, rc):
        """Called when MQTT connection is established"""
        if rc == 0:
            logger.info(f"Connected to MQTT broker at {self.mqtt_broker}:{self.mqtt_port}")
            self.is_connected = True
        else:
            logger.error(f"Failed to connect to MQTT broker. Return code: {rc}")
            self.is_connected = False
    
    def _on_disconnect(self, client, userdata, rc):
        """Called when MQTT connection is lost"""
        self.is_connected = False
        logger.warning(f"Disconnected from MQTT broker. Return code: {rc}")
        if rc != 0:
            logger.info("Attempting to reconnect...")
    
    def start(self):
        """Start reading from leader and sending via MQTT"""
        try:
            # Connect to leader arm
            logger.info(f"Connecting to leader arm on {self.leader.config.port}...")
            self.leader.connect()
            logger.info("Leader arm connected")

            # Connect to MQTT broker
            logger.info(f"Connecting to MQTT broker at {self.mqtt_broker}:{self.mqtt_port}...")
            self.mqtt_client.connect(self.mqtt_broker, self.mqtt_port, keepalive=60)
            self.mqtt_client.loop_start()

            # Wait for MQTT connection
            timeout = 5
            elapsed = 0
            while not self.is_connected and elapsed < timeout:
                time.sleep(0.1)
                elapsed += 0.1

            if not self.is_connected:
                logger.error("Failed to connect to MQTT broker within timeout")
                return

            # Main control loop
            logger.info(f"Leader sender started at {self.fps} FPS")
            logger.info(f"Publishing to topic: {self.mqtt_topic}")
            logger.info("Move the leader arm to control the follower")

            self.is_running = True
            loop_time = 1.0 / self.fps

            last_action = None
            last_send_time = 0.0

            while self.is_running:
                loop_start = time.perf_counter()

                # Read leader position
                action = self.leader.get_action()

                # Throttle: only send if action changed, or 0.25s passed since last send
                now = time.perf_counter()
                should_send = False
                if last_action is None or action != last_action:
                    should_send = True
                elif (now - last_send_time) >= 0.25:
                    should_send = True

                if self.is_connected and should_send:
                    # Format as JSON-RPC message
                    message = {
                        "jsonrpc": "2.0",
                        "id": str(uuid.uuid4()),
                        "method": "set_follower_joint_angles",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "params": {
                            "units": "degrees" if self.leader.config.use_degrees else "normalized",
                            "joints": action
                        }
                    }
                    payload = json.dumps(message)
                    self.mqtt_client.publish(self.mqtt_topic, payload, retain=True)
                    last_action = action.copy() if hasattr(action, 'copy') else dict(action)
                    last_send_time = now
                elif not self.is_connected:
                    logger.warning("Not connected to MQTT, skipping send")

                # Sleep to maintain target FPS
                elapsed = time.perf_counter() - loop_start
                sleep_time = max(0, loop_time - elapsed)
                time.sleep(sleep_time)

        except KeyboardInterrupt:
            logger.info("\nKeyboard interrupt received")
        except Exception as e:
            logger.error(f"Error in control loop: {e}")
        finally:
            self.stop()
    
    def stop(self):
        """Stop the leader sender"""
        if self.is_running:
            logger.info("Stopping leader sender...")
            self.is_running = False
            self.mqtt_client.loop_stop()
            self.mqtt_client.disconnect()
            self.leader.disconnect()
            logger.info("Leader sender stopped")


def parse_args():
    p = argparse.ArgumentParser(description="SO-ARM101 leader sender (MQTT).")
    p.add_argument("--leader-port", default="/dev/ttyACM0")
    p.add_argument("--leader-id", default="so_leader")
    p.add_argument("--mqtt-broker", default="192.168.1.107")
    p.add_argument("--mqtt-port", type=int, default=1883)
    p.add_argument("--mqtt-topic", default="watchman_robotarm/so-101/leader")
    p.add_argument("--fps", type=int, default=60)
    p.add_argument("--use-degrees", action="store_true", default=False)
    return p.parse_args()


def main():
    """Main entry point"""
    args = parse_args()

    sender = LeaderMQTTSender(
        leader_port=args.leader_port,
        leader_id=args.leader_id,
        mqtt_broker=args.mqtt_broker,
        mqtt_port=args.mqtt_port,
        mqtt_topic=args.mqtt_topic,
        fps=args.fps,
        use_degrees=args.use_degrees,
    )

    sender.start()


if __name__ == "__main__":
    main()
