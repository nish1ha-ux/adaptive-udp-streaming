"""
Rate-controller tuning with held-out evaluation.

The default controller thresholds (RTT 80 ms, jitter 20 ms, loss 3%) are lower than the
delays the emulator adds on purpose, so the controller backs off even when sending
slower cannot help. This script searches a small grid of the controller's own
configuration values (no protocol code is edited) and then evaluates the chosen
configuration on different seeds than the ones used to choose it.

Method
------
1. Tuning seeds (101-103): every grid point and the fixed-rate baseline run on every
   condition. The score is the mean ratio  adaptive goodput / fixed goodput.
2. Evaluation seeds (1-5): the best configuration, the default configuration and the
   fixed baseline run again. Only these results are reported as the comparison, so
   the numbers were not used to pick the configuration.

Every grid point is saved, including the ones that did worse.

Usage (from the repository root with the emulator branch checked out)::

    python -m experiments.run_tuned --size-kb 100
"""

import argparse
import itertools
import json
import random
import statistics
import tempfile
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Dict, List, Optional

from experiments.run_experiments import EXPERIMENTS, find_free_port
from src.emulator import EmulatorConfig, UDPRelayServer
from src.rate_control import AdaptiveRateController, RateControllerConfig
from src.receiver import receive_file
from src.paced_sender import send_file

TUNE_SEEDS = (101, 102, 103)
EVAL_SEEDS = (1, 2, 3, 4, 5)
INITIAL_RATE = 200.0  # same starting rate as the fixed-rate baseline
DEFAULT_CFG = {"rtt_threshold_ms": 80.0, "jitter_threshold_ms": 20.0, "loss_threshold": 0.03}
GRID = {
    "rtt_threshold_ms": (80.0, 400.0),
    "jitter_threshold_ms": (20.0, 200.0),
    "loss_threshold": (0.03, 0.15),
}


def trial(exp, mode: str, payload: bytes, cfg: Optional[Dict[str, float]]) -> Dict:
    """Run one transfer through the emulator; ``cfg`` overrides controller thresholds."""
    with tempfile.TemporaryDirectory(prefix="tune_") as tmp:
        src, dst = Path(tmp) / "src.bin", Path(tmp) / "dst.bin"
        src.write_bytes(payload)
        relay_port, recv_port = find_free_port(), find_free_port()
        relay = UDPRelayServer(
            listen_port=relay_port, forward_port=recv_port,
            config=EmulatorConfig(loss_probability=exp.loss_probability, base_delay_ms=exp.base_delay_ms,
                                  jitter_ms=exp.jitter_ms, random_seed=exp.random_seed),
        )
        relay.start()
        thread = threading.Thread(target=receive_file, args=(str(dst),),
                                  kwargs={"port": recv_port, "timeout": 30.0, "send_ack": True}, daemon=True)
        thread.start()
        time.sleep(0.15)
        controller = None
        if mode == "adaptive":
            controller = AdaptiveRateController(RateControllerConfig(
                initial_rate=INITIAL_RATE, min_rate=10.0, max_rate=1000.0,
                min_adjustment_interval_s=0.03, **(cfg or {})))
        try:
            stats = send_file(str(src), port=relay_port, packet_interval=1.0 / INITIAL_RATE, mode=mode,
                              controller=controller, ack_timeout=0.25, max_retransmissions=4, wait_for_acks=True)
            thread.join(timeout=15.0)
            ok = dst.exists() and dst.read_bytes() == payload
        finally:
            relay.stop(drain=False)
        return {"goodput_kbps": stats.goodput_bps / 1000.0, "retransmissions": stats.retransmissions,
                "avg_rtt_ms": stats.average_rtt_ms, "avg_jitter_ms": stats.average_jitter_ms,
                "final_rate_pps": stats.final_rate_pps, "duration_s": stats.duration_s,
                "packets_sent": stats.data_packets_sent, "integrity_verified": ok}


def run_set(payload: bytes, seeds, mode: str, cfg: Optional[Dict[str, float]]) -> Dict[str, List[Dict]]:
    """Run every experiment condition for each seed; returns {experiment name: [trial, ...]}."""
    out: Dict[str, List[Dict]] = {}
    for exp in EXPERIMENTS:
        out[exp.name] = [trial(replace(exp, random_seed=s), mode, payload, cfg) for s in seeds]
        print(f"  {exp.name} | {mode} | {cfg} | "
              f"{statistics.fmean(t['goodput_kbps'] for t in out[exp.name]):.0f} kbps", flush=True)
    return out


def mean(runs: List[Dict], key: str) -> float:
    return statistics.fmean(r[key] for r in runs)


def summarise(name: str, mode: str, runs: List[Dict], exp) -> Dict:
    row = {"experiment": name, "mode": mode, "loss_configured": exp.loss_probability,
           "base_delay_ms": exp.base_delay_ms, "jitter_ms": exp.jitter_ms, "repeats": len(runs),
           "integrity_verified": all(r["integrity_verified"] for r in runs)}
    for key in ("goodput_kbps", "duration_s", "retransmissions", "avg_rtt_ms", "avg_jitter_ms",
                "final_rate_pps", "packets_sent"):
        vals = [r[key] for r in runs]
        row[key] = round(statistics.fmean(vals), 3)
        row[f"{key}_sd"] = round(statistics.stdev(vals), 3) if len(vals) > 1 else 0.0
    return row


def main() -> None:
    parser = argparse.ArgumentParser(description="Tune and evaluate the adaptive rate controller")
    parser.add_argument("--size-kb", type=int, default=100)
    parser.add_argument("--results-dir", default="results")
    args = parser.parse_args()
    payload = bytes(random.Random(1337).getrandbits(8) for _ in range(args.size_kb * 1024))
    started = time.time()

    print("Tuning: fixed baseline", flush=True)
    fixed_tune = run_set(payload, TUNE_SEEDS, "fixed", None)

    grid_results = []
    for values in itertools.product(*GRID.values()):
        cfg = dict(zip(GRID.keys(), values))
        print(f"Tuning: {cfg}", flush=True)
        adaptive = run_set(payload, TUNE_SEEDS, "adaptive", cfg)
        ratios = [mean(adaptive[e.name], "goodput_kbps") / mean(fixed_tune[e.name], "goodput_kbps")
                  for e in EXPERIMENTS]
        grid_results.append({"config": cfg, "score": round(statistics.fmean(ratios), 4),
                             "ratios": [round(r, 3) for r in ratios]})
    best = max(grid_results, key=lambda g: g["score"])["config"]
    print(f"Best configuration on tuning seeds: {best}", flush=True)

    print("Evaluation on held-out seeds", flush=True)
    fixed = run_set(payload, EVAL_SEEDS, "fixed", None)
    default = run_set(payload, EVAL_SEEDS, "adaptive", DEFAULT_CFG)
    tuned = run_set(payload, EVAL_SEEDS, "adaptive", best)

    def table(label_mode: str, runs: Dict[str, List[Dict]]) -> List[Dict]:
        return [summarise(e.name, label_mode, runs[e.name], e) for e in EXPERIMENTS]

    out = {
        "meta": {"size_kb": args.size_kb, "tune_seeds": list(TUNE_SEEDS), "eval_seeds": list(EVAL_SEEDS),
                 "repeats": len(EVAL_SEEDS), "seeds": list(EVAL_SEEDS), "payload_seed": 1337,
                 "initial_rate_pps": INITIAL_RATE, "default_config": DEFAULT_CFG, "tuned_config": best,
                 "grid": GRID, "elapsed_s": round(time.time() - started, 1)},
        "tuning_grid": grid_results,
        "summary": table("fixed", fixed) + table("adaptive", tuned),
        "default_adaptive_summary": table("adaptive", default),
    }
    path = Path(args.results_dir) / "experiment_tuned.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"Wrote {path}")


if __name__ == "__main__":
    main()
