"""
Bridge that will connect to the radio, allowing 
communication from mqtt to the follower arm via UDP over radio.
Also hosts web stream of follower camera on cam.html
"""
import argparse
import socket
import logging
import json
import time
import threading

import paho.mqtt.client as mqtt

# Basic Logging set up
logging.basicConfig(level=logging.INFO,
                    format= "%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

def parse_args():
    p = argparse.ArgumentParser(description="UDP to MQTT bridge for SO-ARM101 telemetry.")
    p.add_argument("--bridge-ip", default="0.0.0.0")
    p.add_argument("--bridge-port", type=int, default=9000)

    p.add_argument("--follower-ip", default="192.168.1.124")
    p.add_argument("--follower-port", type=int, default=9000)

    p.add_argument("--mqtt-broker", default="192.168.1.107")
    p.add_argument("--mqtt-port", type=int, default=1883)
    p.add_argument("--mqtt-topic", default="watchman_robotarm/so-101")

    p.add_argument("--follower-camera-port", type=int, default=5000)
    p.add_argument("--video-stream", action="store_true", help="Whether to start the video stream handler for the follower camera (UDP port 5000)")

    return p.parse_args()

class UDP_MQTT_Bridge:
    def __init__(
        self,
        bridge_ip: str = "0.0.0.0",
        bridge_port: int = 9000,

        follower_ip: str = "192.168.1.124",
        follower_port: int = 9000,

        mqtt_broker: str = "192.168.1.107",
        mqtt_port: int = 1883,
        mqtt_topic: str = "watchman_robotarm/so-101",
    ):
        # UDP config
        self.bridge_sock = None
        self.bridge_ip = bridge_ip
        self.bridge_port = bridge_port

        self.follower_ip = follower_ip
        self.follower_port = follower_port

        # MQTT config
        self.mqtt_client = None
        self.mqtt_broker = mqtt_broker
        self.mqtt_port = mqtt_port
        self.mqtt_topic = mqtt_topic

        self.is_connected = False

    def _on_connect(self, client, userdata, flags, rc):
        """Called when MQTT connection is established"""
        if rc == 0:
            client.subscribe(self.mqtt_topic)
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

    def _on_message(self, _client, _userdata, msg):
        try:
            payload = msg.payload.decode("utf-8")
            message = json.loads(payload)
        except Exception as e:
            logger.warning("Failed to decode MQTT message: %s", e)
            return

        if message.get("method") != "set_follower_joint_angles":
            return

        logger.debug("Forwarding target to follower via UDP: %s:%d", self.follower_ip, self.follower_port)
        self.bridge_sock.sendto(
            payload.encode("utf-8"),
            (self.follower_ip, self.follower_port),
        )
        
    def start(self, stop_event=None):
        # Internet UDP socket bridge_sock 
        try:
            logger.info(f"Starting UDP socket bridge on {self.bridge_ip}:{self.bridge_port} to receive from follower and send to MQTT broker...")
            self.bridge_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.bridge_sock.bind(('0.0.0.0', self.follower_port))
            self.bridge_sock.settimeout(0)
        except Exception as e:
            logger.exception(f"Error in UDP socket: {e} failed to bind to {self.bridge_ip}:{self.bridge_port}")
            return

        # Connect to MQTT broker
        logger.info(f"Connecting to MQTT broker at {self.mqtt_broker}:{self.mqtt_port}...")

        self.mqtt_client = mqtt.Client()
        self.mqtt_client.on_connect = self._on_connect
        self.mqtt_client.on_disconnect = self._on_disconnect
        self.mqtt_client.on_message = self._on_message
        self.mqtt_client.connect(self.mqtt_broker, self.mqtt_port, keepalive=60)
        self.mqtt_client.loop_start()
        
        # Wait for MQTT connection
        timeout = 5
        elapsed = 0
        while not self.is_connected and elapsed < timeout:
            time.sleep(0.1)
            elapsed += 0.1
        
        if not self.is_connected:
            logger.error(F"Failed to connect to MQTT broker within timeout {timeout} seconds")
            return

        logger.info(
            "UDP servo listen %s:%d -> MQTT %s:%d topic %s",
            self.bridge_ip,
            self.bridge_port,
            self.mqtt_broker,
            self.mqtt_port,
            self.mqtt_topic,
        )
        logger.info(
            "MQTT targets -> UDP follower %s:%d",
            self.follower_ip,
            self.follower_port,
        )

        last_follower_servo_positions = None
        last_sent_time = 0.0
        
        # Main control loop
        try:
            while stop_event is None or not stop_event.is_set():
                try:
                    # Receive servo position data from follower via UDP (sent by follower)
                    data, addr = self.bridge_sock.recvfrom(1024) # Recive 1024 bytes from 
                    payload = json.loads(data.decode("utf-8"))
                    method = payload.get("method")
                    logger.debug(f"Received UDP message with method: {method} from follower {addr}")
                    if method == "servo_positions":
                        # Store latest servo positions for state broadcast
                        last_follower_servo_positions = payload.get("joints", {})
                        # Send via MQTT (only if connected)
                        if self.is_connected:
                            mqtt_msg = {
                                "jsonrpc": "2.0",
                                "method": "set_actual_joint_angles",
                                "params": {"joints": payload.get("joints")},
                                "timestamp": time.time(),
                            }
                            self.mqtt_client.publish(self.mqtt_topic, json.dumps(mqtt_msg))
                except socket.error:
                    # No follower servo data received, send last known servo positions to MQTT for frontend display (if enabled)
                    now = time.time()
                    if last_follower_servo_positions and self.is_connected and now - last_sent_time > 1.0: # Send at most once per second
                        mqtt_msg = {
                            "jsonrpc": "2.0",
                            "method": "set_actual_joint_angles",
                            "params": {"joints": last_follower_servo_positions},
                            "timestamp": now,
                        }
                        self.mqtt_client.publish(self.mqtt_topic, json.dumps(mqtt_msg))
                        last_sent_time = now
                    continue
                except Exception:
                    logger.warning("Invalid UDP JSON payload from follower %s", addr)
        finally:
            self.mqtt_client.loop_stop()
            self.mqtt_client.disconnect()

def main():
    args = parse_args()

    bridge = UDP_MQTT_Bridge(
        bridge_ip=args.bridge_ip,
        bridge_port=args.bridge_port,
        mqtt_broker=args.mqtt_broker,
        mqtt_port=args.mqtt_port,
        mqtt_topic=args.mqtt_topic,
        follower_ip=args.follower_ip,
        follower_port=args.follower_port,
    )

    stop_event = threading.Event()
    threads = []

    bridge_thread = threading.Thread(target=bridge.start, args=(stop_event,))
    bridge_thread.start()
    threads.append(bridge_thread)

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