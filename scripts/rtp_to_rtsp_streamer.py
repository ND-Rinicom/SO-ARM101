"""RTP (UDP) to RTSP streamer.

Listens for an incoming H.264 RTP stream on a UDP port and re-publishes it as an
RTSP stream using GStreamer.
"""

import argparse
import logging
import time

import gi

gi.require_version("Gst", "1.0")
gi.require_version("GstRtspServer", "1.0")
from gi.repository import Gst, GstRtspServer, GLib

# Basic Logging set up
logging.basicConfig(level=logging.INFO,
                    format= "%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

def parse_args():
    p = argparse.ArgumentParser(description="RTP (UDP) to RTSP streamer (H.264).")
    p.add_argument("--udp-port", type=int, default=5000, help="UDP port to listen for incoming RTP/H.264")
    p.add_argument("--rtsp-host", default="0.0.0.0", help="RTSP server bind address")
    p.add_argument("--rtsp-port", type=int, default=8000, help="RTSP server port")
    p.add_argument("--mount-point", default="/camera", help="RTSP mount point")
    p.add_argument("--jitter-ms", type=int, default=50, help="RTP jitterbuffer latency in ms")
    return p.parse_args()

# RTSP H.264 streaming server using GStreamer Python bindings
class RtspStreamServer:
    """
    RTSP H.264 streaming server using GStreamer Python bindings.
    Listens for an incoming H.264 RTP stream on a UDP port and re-publishes it as an RTSP stream.
    """

    def __init__(self, udp_port=5000, rtsp_host="127.0.0.1", rtsp_port=8000, mount_point="/camera", jitter_ms=50):
        self.udp_port = udp_port
        self.rtsp_host = rtsp_host
        self.rtsp_port = rtsp_port
        self.mount_point = mount_point
        self.jitter_ms = jitter_ms

    def start(self, stop_event=None):
        Gst.init(None)

        class UDPH264Factory(GstRtspServer.RTSPMediaFactory):
            def __init__(self, udp_port, jitter_ms):
                super().__init__()
                self.udp_port = udp_port
                self.jitter_ms = jitter_ms
                self.set_shared(True)

            def do_create_element(self, url):
                pipeline_str = (
                    f"udpsrc port={self.udp_port} "
                    f"caps=application/x-rtp,media=video,encoding-name=H264,payload=96,clock-rate=90000 ! "
                    f"rtpjitterbuffer latency={self.jitter_ms} ! "
                    f"rtph264depay ! h264parse ! rtph264pay name=pay0 pt=96"
                )
                return Gst.parse_launch(pipeline_str)

        server = GstRtspServer.RTSPServer()
        server.set_address(self.rtsp_host)
        server.set_service(str(self.rtsp_port))
        mount_points = server.get_mount_points()
        factory = UDPH264Factory(self.udp_port, self.jitter_ms)
        mount_points.add_factory(self.mount_point, factory)

        logger.info(f"Starting RTSP server at rtsp://{self.rtsp_host}:{self.rtsp_port}{self.mount_point} (UDP in: {self.udp_port})")

        server.attach(None)

        # Run the GLib main loop in this thread
        loop = GLib.MainLoop()
        try:
            while True:
                if stop_event is not None and stop_event.is_set():
                    logger.info("Stop event set, quitting RTSP server main loop...")
                    loop.quit()
                    break
                loop.get_context().iteration(False)
                time.sleep(0.1)
        except KeyboardInterrupt:
            logger.info("KeyboardInterrupt received, quitting RTSP server main loop...")
            loop.quit()


def main():
    args = parse_args()

    server = RtspStreamServer(
        udp_port=args.udp_port,
        rtsp_host=args.rtsp_host,
        rtsp_port=args.rtsp_port,
        mount_point=args.mount_point,
        jitter_ms=args.jitter_ms,
    )
    server.start(stop_event=None)


if __name__ == "__main__":
    main()