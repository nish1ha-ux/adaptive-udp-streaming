

from __future__ import annotations

import argparse
import csv
import json
import logging
import socket
import sys
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional

from src.emulator import EmulatorConfig, UDPRelayServer
from src.receiver import receive_file
from src.paced_sender import SenderStats, send_file
from src.sender import SenderStats, send_file

logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")


@dataclass
class ExperimentConfig:
    name: str
    loss_probability: float
    base_delay_ms: float
    jitter_ms: float
    random_seed: int = 42


EXPERIMENTS: List[ExperimentConfig] = [
    ExperimentConfig(
        name="E1 - Ideal",
        loss_probability=0.00,
        base_delay_ms=0.0,
        jitter_ms=0.0,
        random_seed=42,
    ),
    ExperimentConfig(
        name="E2 - Mild impairment",
        loss_probability=0.02,
        base_delay_ms=20.0,
        jitter_ms=5.0,
        random_seed=42,
    ),
    ExperimentConfig(
        name="E3 - Moderate impairment",
        loss_probability=0.05,
        base_delay_ms=50.0,
        jitter_ms=15.0,
        random_seed=42,
    ),
    ExperimentConfig(
        name="E4 - High impairment",
        loss_probability=0.10,
        base_delay_ms=100.0,
        jitter_ms=30.0,
        random_seed=42,
    ),
]


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run_single_trial(
    exp: ExperimentConfig,
    mode: str,
    payload_bytes: bytes,
    results_dir: Path,
) -> Dict:
    """Run a single streaming trial with specified emulator settings and transmission mode."""
    temp_dir = results_dir / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)

    source_path = temp_dir / f"src_{mode}.bin"
    output_path = temp_dir / f"dst_{mode}.bin"
    source_path.write_bytes(payload_bytes)

    relay_port = find_free_port()
    recv_port = find_free_port()

    relay_cfg = EmulatorConfig(
        loss_probability=exp.loss_probability,
        base_delay_ms=exp.base_delay_ms,
        jitter_ms=exp.jitter_ms,
        random_seed=exp.random_seed,
    )
    relay = UDPRelayServer(
        listen_port=relay_port,
        forward_port=recv_port,
        config=relay_cfg,
    )
    relay.start()

    recv_thread = threading.Thread(
        target=receive_file,
        args=(str(output_path),),
        kwargs={"port": recv_port, "timeout": 20.0, "send_ack": True},
        daemon=True,
    )
    recv_thread.start()
    time.sleep(0.15)

    try:
        # Pacing: 0.005s (200 pkt/s initial)
        stats: SenderStats = send_file(
            str(source_path),
            port=relay_port,
            packet_interval=0.005,
            mode=mode,
            ack_timeout=0.25,
            max_retransmissions=4,
            wait_for_acks=True,
        )
        recv_thread.join(timeout=10.0)

        integrity_verified = False
        if output_path.exists():
            integrity_verified = (output_path.read_bytes() == payload_bytes)

        return {
            "experiment": exp.name,
            "loss_configured": exp.loss_probability,
            "base_delay_ms": exp.base_delay_ms,
            "jitter_ms": exp.jitter_ms,
            "mode": mode,
            "duration_s": round(stats.duration_s, 3),
            "goodput_kbps": round(stats.goodput_bps / 1000.0, 2),
            "packets_sent": stats.data_packets_sent,
            "retransmissions": stats.retransmissions,
            "acks_received": stats.acks_received,
            "loss_ratio": round(stats.packet_loss_ratio, 4),
            "avg_rtt_ms": round(stats.average_rtt_ms, 2),
            "avg_jitter_ms": round(stats.average_jitter_ms, 2),
            "final_rate_pps": round(stats.final_rate_pps, 1),
            "integrity_verified": integrity_verified,
        }
    finally:
        relay.stop(drain=False)
        # Clean temporary test files
        for f in (source_path, output_path):
            if f.exists():
                try:
                    f.unlink()
                except OSError:
                    pass


def run_all_experiments(
    payload_size_kb: int = 50,
    results_dir: Optional[Path] = None,
) -> List[Dict]:
    """Execute all trials across the experiment matrix and record results."""
    if results_dir is None:
        results_dir = Path(__file__).resolve().parents[1] / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    # Generate reproducible pseudo-random payload of requested size
    import random
    rng = random.Random(1337)
    payload = bytes(rng.getrandbits(8) for _ in range(payload_size_kb * 1024))

    all_results = []
    print(f"\n{'='*75}")
    print(f"RUNNING EVALUATION: FIXED-RATE VS. ADAPTIVE UDP STREAMING ({payload_size_kb} KB)")
    print(f"{'='*75}")

    for exp in EXPERIMENTS:
        print(f"\n>>> Running Experiment: {exp.name}")
        print(f"    Impairments: Loss={exp.loss_probability*100:.1f}%, Delay={exp.base_delay_ms}ms, Jitter={exp.jitter_ms}ms")

        # 1. Run Fixed-Rate Mode
        print("    [1/2] Executing Fixed-Rate Mode...")
        res_fixed = run_single_trial(exp, "fixed", payload, results_dir)
        all_results.append(res_fixed)
        print(f"          Duration: {res_fixed['duration_s']}s | Goodput: {res_fixed['goodput_kbps']} kbps | Retransmissions: {res_fixed['retransmissions']}")

        # 2. Run Adaptive-Rate Mode
        print("    [2/2] Executing Adaptive-Rate Mode (AIMD)...")
        res_adapt = run_single_trial(exp, "adaptive", payload, results_dir)
        all_results.append(res_adapt)
        print(f"          Duration: {res_adapt['duration_s']}s | Goodput: {res_adapt['goodput_kbps']} kbps | Retransmissions: {res_adapt['retransmissions']} | Final Rate: {res_adapt['final_rate_pps']} pps")

    # Save to JSON
    json_path = results_dir / "experiment_results.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2)

    # Save to CSV
    csv_path = results_dir / "experiment_results.csv"
    if all_results:
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(all_results[0].keys()))
            writer.writeheader()
            writer.writerows(all_results)

    # Generate plot if matplotlib is available
    try:
        plot_results(all_results, results_dir / "performance_comparison.png")
    except Exception as exc:
        print(f"Plot generation skipped: {exc}")

    # Print summary table
    print_summary_table(all_results)
    return all_results


def print_summary_table(results: List[Dict]):
    print(f"\n{'='*100}")
    print(f"{'Experiment':<24} | {'Mode':<8} | {'Goodput (kbps)':<14} | {'Duration (s)':<12} | {'Retrans':<8} | {'Avg RTT (ms)':<12} | {'Avg Jitter':<10} | {'Integrity'}")
    print(f"{'-'*100}")
    for r in results:
        integ_str = "PASS" if r["integrity_verified"] else "FAIL"
        print(
            f"{r['experiment']:<24} | {r['mode']:<8} | {r['goodput_kbps']:<14} | {r['duration_s']:<12} | "
            f"{r['retransmissions']:<8} | {r['avg_rtt_ms']:<12} | {r['avg_jitter_ms']:<10} | {integ_str}"
        )
    print(f"{'='*100}\n")


def plot_results(results: List[Dict], output_path: Path):
    """Plot comparative performance charts."""
    import matplotlib.pyplot as plt
    import numpy as np

    experiments = [e.name.split(" - ")[0] for e in EXPERIMENTS]
    x = np.arange(len(experiments))
    width = 0.35

    fixed_goodput = [r["goodput_kbps"] for r in results if r["mode"] == "fixed"]
    adapt_goodput = [r["goodput_kbps"] for r in results if r["mode"] == "adaptive"]

    fixed_retrans = [r["retransmissions"] for r in results if r["mode"] == "fixed"]
    adapt_retrans = [r["retransmissions"] for r in results if r["mode"] == "adaptive"]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))

    # Subplot 1: Goodput
    ax1.bar(x - width/2, fixed_goodput, width, label="Fixed Rate", color="#4C72B0")
    ax1.bar(x + width/2, adapt_goodput, width, label="Adaptive (AIMD)", color="#55A868")
    ax1.set_ylabel("Goodput (kbps)")
    ax1.set_title("Goodput Comparison across Network Conditions")
    ax1.set_xticks(x)
    ax1.set_xticklabels(experiments)
    ax1.legend()
    ax1.grid(True, linestyle="--", alpha=0.6)

    # Subplot 2: Retransmissions
    ax2.bar(x - width/2, fixed_retrans, width, label="Fixed Rate", color="#C44E52")
    ax2.bar(x + width/2, adapt_retrans, width, label="Adaptive (AIMD)", color="#8172B3")
    ax2.set_ylabel("Retransmissions Count")
    ax2.set_title("Retransmission Overhead Comparison")
    ax2.set_xticks(x)
    ax2.set_xticklabels(experiments)
    ax2.legend()
    ax2.grid(True, linestyle="--", alpha=0.6)

    plt.tight_layout()
    plt.savefig(output_path, dpi=200)
    plt.close()
    print(f"Performance chart saved to: {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Run Adaptive UDP Streaming Experiments")
    parser.add_argument("--size-kb", type=int, default=30, help="Payload size in KB (default: 30)")
    parser.add_argument("--results-dir", default=None, help="Directory to save experiment outputs")
    args = parser.parse_args()

    results_dir = Path(args.results_dir) if args.results_dir else None
    run_all_experiments(payload_size_kb=args.size_kb, results_dir=results_dir)


if __name__ == "__main__":
    main()
