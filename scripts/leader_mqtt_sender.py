#!/usr/bin/env python3
"""
MQTT Leader Sender for SO-ARM101
Run this on the PC connected to the leader arm
"""

import json
import logging
import sys
import time
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
        mqtt_topic: str = "watchman_robotarm/so-101",
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
            
            while self.is_running:
                loop_start = time.perf_counter()
                
                # Read leader position
                action = self.leader.get_action()
                
                # Send via MQTT (only if connected)
                if self.is_connected:
                    payload = json.dumps(action)
                    self.mqtt_client.publish(self.mqtt_topic, payload)
                else:
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


def main():
    """Main entry point"""
    # Configuration - EDIT THESE VALUES
    LEADER_PORT = "/dev/ttyACM0"                # Serial port for leader arm
    LEADER_ID = "so_leader"                     # Calibration file ID
    MQTT_BROKER = "192.168.1.107"               # MQTT broker IP address
    MQTT_PORT = 1883                            # MQTT broker port
    MQTT_TOPIC = "watchman_robotarm/so-101"     # MQTT topic to publish to
    FPS = 60                                    # Control loop frequency
    USE_DEGREES = True                         # Use degrees or normalized range
    
    # Create and start sender
    sender = LeaderMQTTSender(
        leader_port=LEADER_PORT,
        leader_id=LEADER_ID,
        mqtt_broker=MQTT_BROKER,
        mqtt_port=MQTT_PORT,
        mqtt_topic=MQTT_TOPIC,
        fps=FPS,
        use_degrees=USE_DEGREES,
    )
    
    sender.start()


if __name__ == "__main__":
    main()
