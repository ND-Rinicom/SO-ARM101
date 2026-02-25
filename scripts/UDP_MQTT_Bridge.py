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

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from PIL import Image
import io
import os

import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst


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

    # Video stream parameters
    p.add_argument("--video-http-host", default="0.0.0.0")
    p.add_argument("--video-http-port", type=int, default=8000)
    p.add_argument("--video-jitter-ms", type=int, default=50)

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

class MjpegStreamServer:
    def __init__(self, udp_port=5000, bind_host="127.0.0.1", bind_port=9001, jitter_ms=50):
        self.udp_port = udp_port
        self.bind_host = bind_host
        self.bind_port = bind_port
        self.jitter_ms = jitter_ms

        self._latest_jpeg = None
        self._jpeg_lock = threading.Lock()
        self._running = False

        self._gst_pipeline = None
        self._appsink = None

    def _make_handler(self):
        srv = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path != "/stream.mjpg":
                    self.send_error(404)
                    return

                self.send_response(200)
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                self.send_header("Pragma", "no-cache")
                self.send_header("Expires", "0")
                self.end_headers()

                while srv._running:
                    with srv._jpeg_lock:
                        frame = srv._latest_jpeg
                    if frame:
                        try:
                            self.wfile.write(b"--frame\r\n")
                            self.wfile.write(b"Content-Type: image/jpeg\r\n")
                            self.wfile.write(f"Content-Length: {len(frame)}\r\n\r\n".encode())
                            self.wfile.write(frame)
                            self.wfile.write(b"\r\n")
                        except BrokenPipeError:
                            break
                    time.sleep(1 / 30)

            def log_message(self, format, *args):
                # silence default HTTP logs
                return

        return Handler

    def _on_new_sample(self, sink):
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.ERROR

        buf = sample.get_buffer()
        caps = sample.get_caps()
        s = caps.get_structure(0)
        width = s.get_value("width")
        height = s.get_value("height")

        ok, mapinfo = buf.map(Gst.MapFlags.READ)
        if not ok:
            return Gst.FlowReturn.ERROR

        try:
            img = Image.frombytes("RGB", (width, height), mapinfo.data, "raw")
            out = io.BytesIO()
            img.save(out, format="JPEG", quality=80)
            jpeg = out.getvalue()
            with self._jpeg_lock:
                self._latest_jpeg = jpeg
        finally:
            buf.unmap(mapinfo)

        return Gst.FlowReturn.OK

    def _build_pipeline(self):
        return (
            f'udpsrc port={self.udp_port} caps="application/x-rtp,media=video,encoding-name=H264,payload=96,clock-rate=90000" ! '
            f'rtpjitterbuffer latency={self.jitter_ms} ! '
            f'rtph264depay ! h264parse ! decodebin ! '
            f'videoconvert ! video/x-raw,format=RGB ! '
            f'appsink name=appsink emit-signals=true sync=false max-buffers=1 drop=true'
        )

    def start(self, stop_event=None):
        Gst.init(None)
        self._running = True

        pipeline_str = self._build_pipeline()
        logger.info("Starting video receiver pipeline: %s", pipeline_str)
        self._gst_pipeline = Gst.parse_launch(pipeline_str)
        self._appsink = self._gst_pipeline.get_by_name("appsink")
        self._appsink.connect("new-sample", self._on_new_sample)
        self._gst_pipeline.set_state(Gst.State.PLAYING)

        server = ThreadingHTTPServer((self.bind_host, self.bind_port), self._make_handler())
        logger.info("MJPEG stream server on http://%s:%d/stream.mjpg", self.bind_host, self.bind_port)

        try:
            server.serve_forever()
        finally:
            self._running = False
            server.server_close()
            if self._gst_pipeline:
                self._gst_pipeline.set_state(Gst.State.NULL)

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

    if args.video_stream:
        video = VideoWebStreamer(
            udp_port=args.follower_camera_port,
            http_host=args.video_http_host,
            http_port=args.video_http_port,
            jitter_ms=args.video_jitter_ms,
        )
        video_thread = threading.Thread(target=video.start, args=(stop_event,), daemon=True)
        video_thread.start()
        threads.append(video_thread)

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