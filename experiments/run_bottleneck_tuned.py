"""
Rate-controller tuning on the bandwidth-limited link, with held-out evaluation.

The default controller (RTT threshold 80 ms, decrease x0.7, +10 pkt/s per step) collapses to
a very low rate on the bottleneck link: queueing pushes the smoothed RTT past the
threshold, the multiplicative decrease fires on every update, and recovery is slow.

This script searches a small grid of the controller's own settings (no protocol code is
edited), then evaluates the winner on seeds that were not used to choose it.

Method
------
1. Tuning seeds (101-103): the fixed 200 pps baseline and every grid point run on all four
   link capacities. Score = mean of (adaptive goodput / fixed goodput) over the conditions.
2. Evaluation seeds (1-5): fixed baseline, default controller and tuned controller run again.
   Only these numbers are reported as the comparison.

Every grid point is stored, including the ones that scored badly. The fixed-rate sweep is
copied from ``results/experiment_bottleneck.json`` (same seeds 1-5, same link).

Usage (repository root, emulator branch checked out)::

    python -m experiments.run_bottleneck_tuned --size-kb 100
"""

import argparse
import itertools
import json
import random
import statistics
import time
from pathlib import Path
from typing import Dict, List

from experiments.run_bottleneck import CONDITIONS, FIXED_RATE_PPS, summarise, trial

TUNE_SEEDS = (101, 102, 103)
EVAL_SEEDS = (1, 2, 3, 4, 5)
DEFAULT_CFG = {"rtt_threshold_ms": 80.0, "decrease_factor": 0.7, "increase_step": 10.0}
GRID = {
    "rtt_threshold_ms": (80.0, 150.0, 250.0),
    "decrease_factor": (0.7, 0.85),
    "increase_step": (10.0, 30.0),
}


def run_set(payload: bytes, seeds, mode: str, cfg: Dict[str, float]) -> Dict[str, List[Dict]]:
    """Run every link condition for each seed; returns {condition name: [trial, ...]}."""
    out = {}
    for name, kbps in CONDITIONS:
        out[name] = [trial(kbps, s, mode, FIXED_RATE_PPS, payload, cfg or None) for s in seeds]
        mean_gp = statistics.fmean(t["goodput_kbps"] for t in out[name])
        print(f"  {name} | {mode} | {cfg} | {mean_gp:.0f} kbps", flush=True)
    return out


def gp(runs: List[Dict]) -> float:
    return statistics.fmean(r["goodput_kbps"] for r in runs)


def main() -> None:
    parser = argparse.ArgumentParser(description="Tune the controller on the bottleneck link")
    parser.add_argument("--size-kb", type=int, default=100)
    parser.add_argument("--results-dir", default="results")
    args = parser.parse_args()
    results_dir = Path(args.results_dir)
    payload = bytes(random.Random(1337).getrandbits(8) for _ in range(args.size_kb * 1024))
    started = time.time()

    print("Tuning: fixed baseline", flush=True)
    fixed_tune = run_set(payload, TUNE_SEEDS, "fixed", {})
    grid_results = []
    for values in itertools.product(*GRID.values()):
        cfg = dict(zip(GRID.keys(), values))
        print(f"Tuning: {cfg}", flush=True)
        adaptive = run_set(payload, TUNE_SEEDS, "adaptive", cfg)
        ratios = [gp(adaptive[n]) / gp(fixed_tune[n]) for n, _ in CONDITIONS]
        grid_results.append({"config": cfg, "score": round(statistics.fmean(ratios), 4),
                             "ratios": [round(r, 3) for r in ratios]})
    best = max(grid_results, key=lambda g: g["score"])["config"]
    print(f"Best configuration on tuning seeds: {best}", flush=True)

    print("Evaluation on held-out seeds", flush=True)
    fixed = run_set(payload, EVAL_SEEDS, "fixed", {})
    default = run_set(payload, EVAL_SEEDS, "adaptive", DEFAULT_CFG)
    tuned = run_set(payload, EVAL_SEEDS, "adaptive", best)

    def table(mode: str, runs: Dict[str, List[Dict]]) -> List[Dict]:
        return [summarise(n, mode, kbps, runs[n]) for n, kbps in CONDITIONS]

    sweep_file = results_dir / "experiment_bottleneck.json"
    sweep = json.loads(sweep_file.read_text(encoding="utf-8")).get("fixed_sweep", []) if sweep_file.is_file() else []
    out = {
        "meta": {"size_kb": args.size_kb, "tune_seeds": list(TUNE_SEEDS), "eval_seeds": list(EVAL_SEEDS),
                 "repeats": len(EVAL_SEEDS), "seeds": list(EVAL_SEEDS), "payload_seed": 1337,
                 "fixed_rate_pps": FIXED_RATE_PPS, "default_config": DEFAULT_CFG, "tuned_config": best,
                 "grid": {k: list(v) for k, v in GRID.items()}, "link": "bandwidth-limited bottleneck",
                 "elapsed_s": round(time.time() - started, 1)},
        "tuning_grid": grid_results,
        "summary": table("fixed", fixed) + table("adaptive", tuned),
        "default_adaptive_summary": table("adaptive", default),
        "fixed_sweep": sweep,
    }
    path = results_dir / "experiment_bottleneck_tuned.json"
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"Wrote {path}")


if __name__ == "__main__":
    main()
