#!/usr/bin/env python3
"""
UDP -> MQTT bridge for SO-ARM101 follower telemetry.
Run this on the laptop to receive UDP servo telemetry from the Pi
and publish it to MQTT for web viewers.

Also includes a ready-to-copy receiver command for the Pi's H.264 RTP/UDP stream (Option A).
(This script does NOT handle video; use GStreamer on the laptop to view/restream.)
"""

import argparse
import json
import logging
import socket
import time

import paho.mqtt.client as mqtt

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def parse_args():
    p = argparse.ArgumentParser(description="UDP to MQTT bridge for SO-ARM101 telemetry.")
    p.add_argument("--udp-servo-listen-host", default="0.0.0.0")
    p.add_argument("--udp-servo-listen-port", type=int, default=9001)

    p.add_argument("--udp-follower-host", default="192.168.1.124")
    p.add_argument("--udp-follower-port", type=int, default=9000)

    p.add_argument("--mqtt-broker", default="192.168.1.107")
    p.add_argument("--mqtt-port", type=int, default=1883)
    p.add_argument("--mqtt-topic", default="watchman_robotarm/so-101")

    # Video stream info (for printing the receiver command)
    p.add_argument("--udp-video-listen-port", type=int, default=5000)
    p.add_argument(
        "--print-video-recv-cmd",
        action="store_true",
        help="Print the GStreamer command to receive the H.264 RTP/UDP stream.",
    )
    return p.parse_args()


def main():
    args = parse_args()

    if args.print_video_recv_cmd:
        cmd = (
            "gst-launch-1.0 -v "
            f'udpsrc port={args.udp_video_listen_port} '
            'caps="application/x-rtp, media=video, encoding-name=H264, payload=96" ! '
            "rtph264depay ! avdec_h264 ! videoconvert ! autovideosink"
        )
        print("\nH.264 RTP/UDP receiver command (run on this laptop):\n")
        print(cmd)
        print("")
        # continue running bridge too (don’t exit)

    # UDP sockets
    servo_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    servo_sock.bind((args.udp_servo_listen_host, args.udp_servo_listen_port))
    servo_sock.settimeout(1.0)

    follower_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    # MQTT client
    client = mqtt.Client()

    def _on_connect(_client, _userdata, _flags, rc):
        if rc == 0:
            _client.subscribe(args.mqtt_topic)
            logger.info("Connected to MQTT broker and subscribed to topic: %s", args.mqtt_topic)
        else:
            logger.error("Failed MQTT connect: %s", rc)

    def _on_message(_client, _userdata, msg):
        try:
            payload = msg.payload.decode("utf-8")
            message = json.loads(payload)
        except Exception as e:
            logger.warning("Failed to decode MQTT message: %s", e)
            return

        if message.get("method") != "set_follower_joint_angles":
            return

        logger.debug("Forwarding target to follower via UDP: %s:%d", args.udp_follower_host, args.udp_follower_port)
        follower_sock.sendto(
            payload.encode("utf-8"),
            (args.udp_follower_host, args.udp_follower_port),
        )

    client.on_connect = _on_connect
    client.on_message = _on_message
    client.connect(args.mqtt_broker, args.mqtt_port, keepalive=60)
    client.loop_start()

    logger.info(
        "UDP servo listen %s:%d -> MQTT %s:%d topic %s",
        args.udp_servo_listen_host,
        args.udp_servo_listen_port,
        args.mqtt_broker,
        args.mqtt_port,
        args.mqtt_topic,
    )
    logger.info(
        "MQTT targets -> UDP follower %s:%d",
        args.udp_follower_host,
        args.udp_follower_port,
    )
    logger.info(
        "Video receive (manual): gst-launch-1.0 -v udpsrc port=%d caps=\"application/x-rtp, media=video, encoding-name=H264, payload=96\" ! "
        "rtph264depay ! avdec_h264 ! videoconvert ! autovideosink",
        args.udp_video_listen_port,
    )
    logger.info("Bridge ready. Waiting for UDP servo data...")

    servo_msg_count = 0

    try:
        while True:
            try:
                data, addr = servo_sock.recvfrom(65535)
            except socket.timeout:
                continue

            try:
                payload = json.loads(data.decode("utf-8"))
            except Exception:
                logger.warning("Invalid UDP JSON payload from %s", addr)
                continue

            if payload.get("type") != "servo":
                continue

            joints = payload.get("joints") or {}
            if not joints:
                continue

            servo_msg_count += 1
            if servo_msg_count % 60 == 0:
                logger.info("← Received servo telemetry from %s (count: %d)", addr, servo_msg_count)

            mqtt_msg = {
                "jsonrpc": "2.0",
                "method": "set_actual_joint_angles",
                "params": {"joints": joints},
                "timestamp": time.time(),
            }
            client.publish(args.mqtt_topic, json.dumps(mqtt_msg))

    except KeyboardInterrupt:
        logger.info("Stopping UDP->MQTT bridge")
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
