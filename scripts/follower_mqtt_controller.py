#!/usr/bin/env python3
"""
MQTT-controlled SO-ARM101 Follower with Jump Protection
Run this on the Raspberry Pi connected to the follower arm
"""

import json
import logging
import sys
from pathlib import Path

# Add parent directory to path to import lerobot
sys.path.insert(0, str(Path(__file__).parent.parent))

import paho.mqtt.client as mqtt
from lerobot.robots.so_follower import SO101Follower, SO101FollowerConfig
from lerobot.robots.utils import ensure_safe_goal_position

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class FollowerSafetyController:
    """
    MQTT-based follower controller with local jump protection.
    
    Safety features:
    - Reads current position locally (fast USB)
    - Clamps jumps based on max_relative_target
    - Works even with slow/unreliable network
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
    ):
        """
        Args:
            follower_port: Serial port for follower arm (e.g., "/dev/ttyACM0")
            follower_id: ID for calibration file (e.g., "so_follower")
            mqtt_broker: MQTT broker address (e.g., "192.168.1.107")
            mqtt_port: MQTT broker port (default: 1883)
            mqtt_topic: MQTT topic to subscribe to for targets
            max_relative_target: Maximum position jump per step (degrees or normalized units)
            use_degrees: Use degrees (True) or normalized -100 to 100 range (False)
        """
        # Initialize follower
        follower_config = SO101FollowerConfig(
            port=follower_port,
            id=follower_id,
            max_relative_target=max_relative_target,
            use_degrees=use_degrees,
        )
        self.follower = SO101Follower(follower_config)
        self.max_relative_target = max_relative_target
        
        # Initialize MQTT
        self.mqtt_client = mqtt.Client()
        self.mqtt_client.on_connect = self._on_connect
        self.mqtt_client.on_message = self._on_message
        self.mqtt_client.on_disconnect = self._on_disconnect
        self.mqtt_broker = mqtt_broker
        self.mqtt_port = mqtt_port
        self.mqtt_topic = mqtt_topic
        
        self.is_running = False
        
    def _on_connect(self, client, userdata, flags, rc):
        """Called when MQTT connection is established"""
        if rc == 0:
            logger.info(f"Connected to MQTT broker at {self.mqtt_broker}:{self.mqtt_port}")
            client.subscribe(self.mqtt_topic)
            logger.info(f"Subscribed to topic: {self.mqtt_topic}")
        else:
            logger.error(f"Failed to connect to MQTT broker. Return code: {rc}")
    
    def _on_disconnect(self, client, userdata, rc):
        """Called when MQTT connection is lost"""
        logger.warning(f"Disconnected from MQTT broker. Return code: {rc}")
        if rc != 0:
            logger.info("Attempting to reconnect...")
    
    def _on_message(self, client, userdata, msg):
        """
        Called when MQTT target position arrives.
        Applies jump protection and sends safe command to servos.
        """
        try:
            # Parse target action
            target_action = json.loads(msg.payload.decode())
            
            # Extract goal positions (remove .pos suffix)
            goal_pos = {
                key.removesuffix(".pos"): val 
                for key, val in target_action.items() 
                if key.endswith(".pos")
            }
            
            if not goal_pos:
                logger.warning("Received empty action, skipping")
                return
            
            # Read CURRENT position from follower (local USB - fast!)
            present_pos = self.follower.bus.sync_read("Present_Position")
            
            # Apply jump protection
            if self.max_relative_target is not None:
                goal_present_pos = {
                    key: (g_pos, present_pos[key]) 
                    for key, g_pos in goal_pos.items()
                }
                safe_goal_pos = ensure_safe_goal_position(
                    goal_present_pos, 
                    self.max_relative_target
                )
            else:
                safe_goal_pos = goal_pos
            
            # Send safe command to servos
            self.follower.bus.sync_write("Goal_Position", safe_goal_pos)
            
        except json.JSONDecodeError:
            logger.error(f"Failed to parse JSON: {msg.payload}")
        except Exception as e:
            logger.error(f"Error processing message: {e}")
    
    def start(self):
        """Start the follower controller"""
        try:
            # Connect to follower arm
            logger.info(f"Connecting to follower arm on {self.follower.config.port}...")
            self.follower.connect()
            logger.info("Follower arm connected")
            
            # Connect to MQTT broker
            logger.info(f"Connecting to MQTT broker at {self.mqtt_broker}:{self.mqtt_port}...")
            self.mqtt_client.connect(self.mqtt_broker, self.mqtt_port, keepalive=60)
            
            # Start MQTT loop
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
        """Stop the follower controller"""
        if self.is_running:
            logger.info("Stopping follower controller...")
            self.mqtt_client.loop_stop()
            self.mqtt_client.disconnect()
            self.follower.disconnect()
            self.is_running = False
            logger.info("Follower controller stopped")


def main():
    """Main entry point"""
    # Configuration - EDIT THESE VALUES
    FOLLOWER_PORT = "/dev/ttyACM0"           # Serial port for follower arm
    FOLLOWER_ID = "so_follower"                     # Calibration file ID
    MQTT_BROKER = "192.168.1.107"            # MQTT broker IP address
    MQTT_PORT = 1883                          # MQTT broker port
    MQTT_TOPIC = "watchman_robotarm/so-101"     # MQTT topic to subscribe to
    MAX_RELATIVE_TARGET = 20.0               # Max jump per step (degrees or normalized)
    USE_DEGREES = True                       # Use degrees or normalized range
    
    # Create and start controller
    controller = FollowerSafetyController(
        follower_port=FOLLOWER_PORT,
        follower_id=FOLLOWER_ID,
        mqtt_broker=MQTT_BROKER,
        mqtt_port=MQTT_PORT,
        mqtt_topic=MQTT_TOPIC,
        max_relative_target=MAX_RELATIVE_TARGET,
        use_degrees=USE_DEGREES,
    )
    
    controller.start()


if __name__ == "__main__":
    main()
