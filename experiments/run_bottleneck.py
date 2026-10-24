"""
Fixed vs. adaptive sending over a bandwidth-limited bottleneck.

Uses ``experiments/bottleneck_link.py``, which adds the bandwidth-limited queue on top of
the existing emulator (``src/emulator.py`` is not modified); see ``docs/bandwidth_emulator_plan.md``.

Conditions, seeds and the success rule are fixed in the plan before any run.
Writes ``results/experiment_bottleneck.json`` in the shape the dashboard reads
(``meta`` + ``summary``), plus a ``fixed_sweep`` section with other fixed rates.

Usage (repository root, emulator branch with the change checked out)::

    python -m experiments.run_bottleneck --size-kb 100 --repeats 5
"""

import argparse
import json
import random
import statistics
import tempfile
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

from experiments.run_experiments import find_free_port
from experiments.bottleneck_link import BottleneckRelay
from src.emulator import EmulatorConfig
from src.rate_control import AdaptiveRateController, RateControllerConfig
from src.receiver import receive_file
from src.paced_sender import send_file

BASE_DELAY_MS = 20.0
JITTER_MS = 5.0
QUEUE_PACKETS = 50
FIXED_RATE_PPS = 200.0
SWEEP_RATES_PPS = (25.0, 50.0, 100.0, 200.0, 400.0)
CONDITIONS = (
    ("B1 - 5000 kbps link", 5000),
    ("B2 - 2000 kbps link", 2000),
    ("B3 - 1000 kbps link", 1000),
    ("B4 - 500 kbps link", 500),
)
METRICS = ("goodput_kbps", "duration_s", "retransmissions", "avg_rtt_ms", "avg_jitter_ms",
           "final_rate_pps", "packets_sent")


def trial(bandwidth_kbps: int, seed: int, mode: str, rate_pps: float, payload: bytes,
          controller_cfg: Optional[Dict[str, float]] = None) -> Dict:
    """One transfer through the bottleneck. ``mode`` is 'fixed' or 'adaptive'.

    ``controller_cfg`` overrides rate-controller settings (adaptive mode only).
    """
    with tempfile.TemporaryDirectory(prefix="bneck_") as tmp:
        src, dst = Path(tmp) / "src.bin", Path(tmp) / "dst.bin"
        src.write_bytes(payload)
        relay_port, recv_port = find_free_port(), find_free_port()
        relay = BottleneckRelay(
            listen_port=relay_port, forward_port=recv_port,
            config=EmulatorConfig(base_delay_ms=BASE_DELAY_MS, jitter_ms=JITTER_MS, random_seed=seed),
            bandwidth_kbps=bandwidth_kbps, queue_packets=QUEUE_PACKETS,
        )
        relay.start()
        thread = threading.Thread(target=receive_file, args=(str(dst),),
                                  kwargs={"port": recv_port, "timeout": 60.0, "send_ack": True}, daemon=True)
        thread.start()
        time.sleep(0.15)
        controller: Optional[AdaptiveRateController] = None
        if mode == "adaptive":
            controller = AdaptiveRateController(RateControllerConfig(
                initial_rate=rate_pps, min_rate=10.0, max_rate=1000.0, min_adjustment_interval_s=0.03,
                **(controller_cfg or {})))
        try:
            stats = send_file(str(src), port=relay_port, packet_interval=1.0 / rate_pps, mode=mode,
                              controller=controller, ack_timeout=0.25, max_retransmissions=6,
                              wait_for_acks=True)
            thread.join(timeout=30.0)
            ok = dst.exists() and dst.read_bytes() == payload
        finally:
            relay.stop(drain=False)
        return {"goodput_kbps": stats.goodput_bps / 1000.0, "duration_s": stats.duration_s,
                "retransmissions": stats.retransmissions, "avg_rtt_ms": stats.average_rtt_ms,
                "avg_jitter_ms": stats.average_jitter_ms, "final_rate_pps": stats.final_rate_pps,
                "packets_sent": stats.data_packets_sent, "integrity_verified": ok}


def summarise(name: str, mode: str, bandwidth_kbps: int, runs: List[Dict], extra: Optional[Dict] = None) -> Dict:
    """Mean and standard deviation across repeats, in the dashboard's row shape."""
    row = {"experiment": name, "mode": mode, "loss_configured": 0.0, "base_delay_ms": BASE_DELAY_MS,
           "jitter_ms": JITTER_MS, "bandwidth_kbps": bandwidth_kbps, "repeats": len(runs),
           "integrity_verified": all(r["integrity_verified"] for r in runs)}
    for key in METRICS:
        vals = [r[key] for r in runs]
        row[key] = round(statistics.fmean(vals), 3)
        row[f"{key}_sd"] = round(statistics.stdev(vals), 3) if len(vals) > 1 else 0.0
    row.update(extra or {})
    return row


def main() -> None:
    parser = argparse.ArgumentParser(description="Fixed vs adaptive over a bandwidth-limited link")
    parser.add_argument("--size-kb", type=int, default=100)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--results-dir", default="results")
    args = parser.parse_args()

    seeds = list(range(1, args.repeats + 1))
    payload = bytes(random.Random(1337).getrandbits(8) for _ in range(args.size_kb * 1024))
    started = time.time()
    summary: List[Dict] = []
    sweep: List[Dict] = []

    for name, kbps in CONDITIONS:
        for mode in ("fixed", "adaptive"):
            runs = [trial(kbps, s, mode, FIXED_RATE_PPS, payload) for s in seeds]
            summary.append(summarise(name, mode, kbps, runs))
            print(f"{name} | {mode:8} | {summary[-1]['goodput_kbps']:.0f} kbps", flush=True)
        for rate in SWEEP_RATES_PPS:
            runs = [trial(kbps, s, "fixed", rate, payload) for s in seeds]
            sweep.append(summarise(name, "fixed", kbps, runs, {"rate_pps": rate}))
            print(f"{name} | fixed {rate:>5.0f} pps | {sweep[-1]['goodput_kbps']:.0f} kbps", flush=True)

    out = {
        "meta": {"size_kb": args.size_kb, "repeats": args.repeats, "seeds": seeds, "payload_seed": 1337,
                 "base_delay_ms": BASE_DELAY_MS, "jitter_ms": JITTER_MS, "queue_packets": QUEUE_PACKETS,
                 "fixed_rate_pps": FIXED_RATE_PPS, "sweep_rates_pps": list(SWEEP_RATES_PPS),
                 "elapsed_s": round(time.time() - started, 1)},
        "summary": summary,
        "fixed_sweep": sweep,
    }
    path = Path(args.results_dir) / "experiment_bottleneck.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"Wrote {path}")


if __name__ == "__main__":
    main()
