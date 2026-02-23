#!/usr/bin/env python3
"""
UDP-controlled SO-ARM101 Follower with Jump Protection
Sends servo telemetry via UDP + H.264 RTP video over UDP (Option A / GStreamer).
Run this on the Raspberry Pi connected to the follower arm.

Video pipeline:
  v4l2src -> (raw) -> encoder -> rtph264pay -> udpsink
"""

import argparse
import json
import logging
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

# Add parent directory to path to import lerobot
sys.path.insert(0, str(Path(__file__).parent.parent))

from lerobot.robots.so_follower import SO101Follower, SO101FollowerConfig
from lerobot.robots.robot import ensure_safe_goal_position

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def start_gst_h264_rtp_udp(
    device: str,
    host: str,
    port: int,
    resolution: str = "640x480",
    fps: int = 30,
    bitrate_kbps: int = 1500,
) -> subprocess.Popen:
    """
    Start GStreamer to send H.264 via RTP over UDP.
    Returns a Popen handle so we can terminate it on exit.

    Receiver example:
      gst-launch-1.0 -v \
        udpsrc port=5000 caps="application/x-rtp, media=video, encoding-name=H264, payload=96" ! \
        rtph264depay ! avdec_h264 ! videoconvert ! autovideosink
    """
    if shutil.which("gst-launch-1.0") is None:
        raise RuntimeError("gst-launch-1.0 not found. Install with: sudo apt install -y gstreamer1.0-tools")

    # Parse resolution "WIDTHxHEIGHT"
    try:
        w_str, h_str = resolution.lower().split("x")
        width, height = int(w_str), int(h_str)
    except Exception as e:
        raise ValueError(f"Invalid resolution '{resolution}'. Expected like 640x480") from e

    # Prefer hardware encoder if available (often present on Pi OS builds)
    # Fallback to x264enc otherwise.
    have_v4l2h264enc = shutil.which("v4l2-ctl") is not None  # weak signal, but fine
    # We can't reliably probe plugins without gst-inspect, so we just try v4l2h264enc first.
    # If it fails immediately, user will see stdout and can switch by installing needed plugins.
    use_hw = True

    if use_hw:
        pipeline = (
            f'v4l2src device={device} ! '
            f'video/x-raw,width={width},height={height},framerate={fps}/1 ! '
            f'videoconvert ! '
            f'v4l2h264enc extra-controls="controls,video_bitrate={bitrate_kbps * 1000}" ! '
            f'h264parse config-interval=1 ! '
            f'rtph264pay config-interval=1 pt=96 ! '
            f'udpsink host={host} port={port} sync=false async=false'
        )
    else:
        pipeline = (
            f'v4l2src device={device} ! '
            f'video/x-raw,width={width},height={height},framerate={fps}/1 ! '
            f'videoconvert ! '
            f'x264enc tune=zerolatency bitrate={bitrate_kbps} speed-preset=ultrafast key-int-max={fps} ! '
            f'rtph264pay config-interval=1 pt=96 ! '
            f'udpsink host={host} port={port} sync=false async=false'
        )

    cmd = ["gst-launch-1.0", "-v"] + pipeline.split(" ")

    logger.info("Starting GStreamer H264 RTP UDP: %s", " ".join(cmd))
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )

    time.sleep(0.6)
    if proc.poll() is not None:
        out = ""
        try:
            out = (proc.stdout.read() or "").strip() if proc.stdout else ""
        except Exception:
            pass

        # If hardware encoder pipeline failed, try software x264 automatically once.
        logger.warning("GStreamer pipeline exited immediately (likely missing v4l2h264enc). Falling back to x264enc.")
        try:
            fallback_pipeline = (
                f'v4l2src device={device} ! '
                f'video/x-raw,width={width},height={height},framerate={fps}/1 ! '
                f'videoconvert ! '
                f'x264enc tune=zerolatency bitrate={bitrate_kbps} speed-preset=ultrafast key-int-max={fps} ! '
                f'rtph264pay config-interval=1 pt=96 ! '
                f'udpsink host={host} port={port} sync=false async=false'
            )
            fallback_cmd = ["gst-launch-1.0", "-v"] + fallback_pipeline.split(" ")
            proc = subprocess.Popen(
                fallback_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )
            time.sleep(0.6)
            if proc.poll() is not None:
                out2 = ""
                try:
                    out2 = (proc.stdout.read() or "").strip() if proc.stdout else ""
                except Exception:
                    pass
                raise RuntimeError(
                    "GStreamer exited immediately (both hw + sw attempts).\n"
                    f"HW attempt output:\n{out}\n\nSW attempt output:\n{out2}"
                )
            logger.info("GStreamer started (pid=%s). H264 RTP UDP: udp://%s:%d", proc.pid, host, port)
            return proc
        except Exception:
            raise RuntimeError(f"GStreamer exited immediately.\nOutput:\n{out}")

    logger.info("GStreamer started (pid=%s). H264 RTP UDP: udp://%s:%d", proc.pid, host, port)
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
    UDP-based follower controller with local jump protection.
    Sends servo telemetry via UDP + H.264 RTP video over UDP.
    """

    def __init__(
        self,
        follower_port: str = "/dev/ttyACM0",
        follower_id: str = "so_follower",
        udp_host: str = "192.168.1.107",
        udp_target_port: int = 9000,
        udp_servo_port: int = 9001,
        udp_video_port: int = 5000,
        max_relative_target: float = 20.0,
        use_degrees: bool = True,
        camera_device: Optional[str] = None,
        camera_resolution: str = "640x480",
        camera_fps: int = 30,
        camera_bitrate_kbps: int = 1500,
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

        # Camera config (H.264 RTP over UDP via GStreamer)
        self.camera_device = camera_device
        self.camera_resolution = camera_resolution
        self.camera_fps = camera_fps
        self.camera_bitrate_kbps = camera_bitrate_kbps
        self._camera_proc: Optional[subprocess.Popen] = None

        # UDP config
        self.udp_host = udp_host
        self.udp_target_port = udp_target_port
        self.udp_servo_port = udp_servo_port
        self.udp_video_port = udp_video_port
        self._servo_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._target_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        self.is_running = False

    def _send_servo_udp(self, present_pos: dict) -> None:
        payload = json.dumps(
            {
                "type": "servo",
                "timestamp": time.time(),
                "joints": {f"{k}.pos": v for k, v in present_pos.items()},
            }
        ).encode("utf-8")
        self._servo_sock.sendto(payload, (self.udp_host, self.udp_servo_port))

    def on_target_message(self, payload: str) -> None:
        try:
            message = json.loads(payload)

            method = message.get("method")
            if method != "set_follower_joint_angles":
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

            try:
                self._send_servo_udp(present_pos)
            except Exception as e:
                logger.warning("Failed to send present_pos over UDP: %s", e)

            if self.max_relative_target is not None:
                goal_present_pos = {key: (g_pos, present_pos[key]) for key, g_pos in goal_pos.items()}
                safe_goal_pos = ensure_safe_goal_position(goal_present_pos, self.max_relative_target)
            else:
                safe_goal_pos = goal_pos

            self.follower.bus.sync_write("Goal_Position", safe_goal_pos)

        except json.JSONDecodeError:
            logger.error("Failed to parse JSON: %s", payload)
        except Exception as e:
            logger.error("Error processing message: %s", e)

    def start(self):
        try:
            # Optional camera (H.264 RTP over UDP via GStreamer)
            if self.camera_device:
                self._camera_proc = start_gst_h264_rtp_udp(
                    device=self.camera_device,
                    host=self.udp_host,
                    port=self.udp_video_port,
                    resolution=self.camera_resolution,
                    fps=self.camera_fps,
                    bitrate_kbps=self.camera_bitrate_kbps,
                )

            # Connect to follower arm
            logger.info("Connecting to follower arm on %s...", self.follower.config.port)
            self.follower.connect()
            logger.info("Follower arm connected")

            logger.info("Follower controller started. Waiting for targets...")
            logger.info("Jump protection: max_relative_target = %s", self.max_relative_target)
            logger.info(
                "UDP target listen: 0.0.0.0:%d | Servo UDP target: %s:%d | Video RTP/H264 UDP target: %s:%d",
                self.udp_target_port,
                self.udp_host,
                self.udp_servo_port,
                self.udp_host,
                self.udp_video_port,
            )

            self.is_running = True
            self._target_sock.bind(("0.0.0.0", self.udp_target_port))
            self._target_sock.settimeout(1.0)

            while self.is_running:
                try:
                    data, _addr = self._target_sock.recvfrom(65535)
                except socket.timeout:
                    continue
                self.on_target_message(data.decode("utf-8"))

        except KeyboardInterrupt:
            logger.info("\nKeyboard interrupt received")
            self.stop()
        except Exception as e:
            logger.error("Error starting controller: %s", e)
            self.stop()

    def stop(self):
        if self.is_running:
            logger.info("Stopping follower controller...")
            try:
                self.follower.disconnect()
            except Exception:
                pass
            self.is_running = False

        stop_process(self._camera_proc, "gstreamer")
        logger.info("Follower controller stopped")


def parse_args():
    p = argparse.ArgumentParser(
        description="SO-ARM101 follower (UDP) with optional H.264 RTP camera stream over UDP (GStreamer)."
    )
    p.add_argument("--follower-port", default="/dev/ttyACM0")
    p.add_argument("--follower-id", default="so_follower")
    p.add_argument("--udp-host", default="192.168.1.107")
    p.add_argument("--udp-target-port", type=int, default=9000)
    p.add_argument("--udp-servo-port", type=int, default=9001)
    p.add_argument("--udp-video-port", type=int, default=5000)
    p.add_argument("--max-relative-target", type=float, default=20.0)
    p.add_argument("--use-degrees", action="store_true", default=True)

    # Optional camera
    p.add_argument(
        "--camera",
        dest="camera_device",
        default=None,
        help="Enable H.264 RTP over UDP from this V4L2 device, e.g. /dev/video0",
    )
    p.add_argument("--cam-res", dest="camera_resolution", default="640x480")
    p.add_argument("--cam-fps", dest="camera_fps", type=int, default=30)
    p.add_argument("--cam-bitrate-kbps", dest="camera_bitrate_kbps", type=int, default=1500)

    return p.parse_args()


def main():
    args = parse_args()

    controller = FollowerSafetyController(
        follower_port=args.follower_port,
        follower_id=args.follower_id,
        udp_host=args.udp_host,
        udp_target_port=args.udp_target_port,
        udp_servo_port=args.udp_servo_port,
        udp_video_port=args.udp_video_port,
        max_relative_target=args.max_relative_target,
        use_degrees=args.use_degrees,
        camera_device=args.camera_device,
        camera_resolution=args.camera_resolution,
        camera_fps=args.camera_fps,
        camera_bitrate_kbps=args.camera_bitrate_kbps,
    )

    controller.start()


if __name__ == "__main__":
    main()
