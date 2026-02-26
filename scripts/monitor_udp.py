"""
Monitor UDP throughput by destination port using Scapy.
Run with optional --ports to filter specific destination ports (e.g. --ports 9000 5000 or --ports 9000,5000).
Displays elapsed time, average kbps, max kbps, min nonzero kbps, and last interval kbps for each port and totals.

Requires root/administrator privileges to run (e.g. sudo python monitor_udp.py).
To do this using venv without installing Scapy globally, you can run:
sudo /home/nialldorrington/Documents/SO-ARM101-Digital-Twin/lerobot-venv/bin/python ./scripts/monitor_udp.py --ports 9000 5000
sudo [python path] ./scripts/monitor_udp.py --ports 9000 5000
"""


import time
import threading
import argparse
from dataclasses import dataclass
from typing import Optional, Dict, Set

from scapy.all import sniff, UDP  # type: ignore


@dataclass
class PortStats:
    first_seen: float
    total_bytes: int = 0

    interval_bytes: int = 0
    last_kbps: float = 0.0

    max_kbps: float = 0.0
    min_kbps_nonzero: Optional[float] = None

    def finalize_interval(self) -> float:
        """Compute last interval kbps, update min/max (nonzero), reset interval_bytes."""
        kbps = (self.interval_bytes * 8) / 1000.0
        self.last_kbps = kbps

        if kbps > 0:
            if self.min_kbps_nonzero is None or kbps < self.min_kbps_nonzero:
                self.min_kbps_nonzero = kbps
            if kbps > self.max_kbps:
                self.max_kbps = kbps

        self.interval_bytes = 0
        return kbps

    def elapsed_s(self, now: float) -> int:
        return max(0, int(now - self.first_seen))

    def avg_kbps(self, now: float) -> float:
        secs = max(now - self.first_seen, 1e-9)
        return (self.total_bytes * 8) / 1000.0 / secs


def parse_ports_arg(ports_list) -> Optional[Set[int]]:
    """Accept: --ports 9000 5000  or  --ports 9000,5000"""
    if not ports_list:
        return None
    out: Set[int] = set()
    for item in ports_list:
        for part in str(item).split(","):
            part = part.strip()
            if not part:
                continue
            p = int(part)
            if p < 1 or p > 65535:
                raise ValueError(f"Invalid port: {p}")
            out.add(p)
    return out if out else None


def main():
    ap = argparse.ArgumentParser(description="Monitor UDP throughput by destination port.")
    ap.add_argument(
        "--ports",
        nargs="*",
        default=None,
        help="Optional list of destination UDP ports to display (e.g. --ports 9000 5000 or --ports 9000,5000).",
    )
    ap.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Refresh interval in seconds (default: 1.0).",
    )
    args = ap.parse_args()
    port_filter = parse_ports_arg(args.ports)

    lock = threading.Lock()
    stats: Dict[int, PortStats] = {}

    start_time = time.time()

    def process_packet(pkt):
        if UDP not in pkt:
            return

        dport = int(pkt[UDP].dport)

        if port_filter is not None and dport not in port_filter:
            return

        # UDP payload size (bytes)
        payload_len = len(bytes(pkt[UDP].payload))
        now = time.time()

        with lock:
            s = stats.get(dport)
            if s is None:
                stats[dport] = PortStats(
                    first_seen=now,
                    total_bytes=payload_len,
                    interval_bytes=payload_len,
                )
            else:
                s.total_bytes += payload_len
                s.interval_bytes += payload_len

    def printer():
        while True:
            time.sleep(args.interval)
            now = time.time()

            with lock:
                # finalize intervals (updates last/min/max and resets interval_bytes)
                for s in stats.values():
                    s.finalize_interval()

                rows = []
                total_bytes_all = 0
                total_last = 0.0
                total_max = 0.0
                total_min = None  # min nonzero among ports

                for port, s in stats.items():
                    elapsed = s.elapsed_s(now)
                    avg = s.avg_kbps(now)
                    mx = s.max_kbps
                    mn = s.min_kbps_nonzero if s.min_kbps_nonzero is not None else 0.0
                    last = s.last_kbps

                    rows.append((port, elapsed, avg, mx, mn, last))

                    total_bytes_all += s.total_bytes
                    total_last += last
                    total_max = max(total_max, mx)
                    if s.min_kbps_nonzero is not None:
                        total_min = s.min_kbps_nonzero if total_min is None else min(total_min, s.min_kbps_nonzero)

            total_elapsed = int(now - start_time)
            total_avg = (total_bytes_all * 8) / 1000.0 / max(total_elapsed, 1)
            total_min_val = total_min if total_min is not None else 0.0

            print("\033c", end="")  # clear screen
            print(
                f"{'PORT':<6}"
                f"{'Elapsed (s)':<14}"
                f"{'Avg Kbps':<12}"
                f"{'Max Kbps':<10}"
                f"{'Min Kbps':<10}"
                f"{'Last Kbps':<10}"
            )

            for port, elapsed, avg, mx, mn, last in sorted(rows, key=lambda x: x[0]):
                print(f"{port:<6}{elapsed:<14}{avg:<12.2f}{mx:<10.2f}{mn:<10.2f}{last:<10.2f}")

            print(f"{'TOTAL':<6}{total_elapsed:<14}{total_avg:<12.2f}{total_max:<10.2f}{total_min_val:<10.2f}{total_last:<10.2f}")

    t = threading.Thread(target=printer, daemon=True)
    t.start()

    sniff(filter="udp", prn=process_packet, store=False)


if __name__ == "__main__":
    main()