"""
Repeated fixed-vs-adaptive experiments with summary statistics.

Wraps the existing single-trial runner (``experiments/run_experiments.py``) so that
every condition is executed several times with different emulator seeds. Each
seed is used for both modes, so fixed and adaptive face the same loss pattern.

Nothing is modified or invented here: the numbers come from the real sender,
receiver and emulator. The script only repeats trials and aggregates them.

Usage (from the repository root)::

    python -m experiments.run_repeats --size-kb 100 --repeats 5
"""

import argparse
import json
import random
import statistics
import time
from dataclasses import replace
from pathlib import Path
from typing import Dict, List

from experiments.run_experiments import EXPERIMENTS, run_single_trial

MODES = ("fixed", "adaptive")
# Numeric fields summarised as mean / standard deviation across repeats.
METRICS = (
    "goodput_kbps", "duration_s", "retransmissions", "avg_rtt_ms",
    "avg_jitter_ms", "final_rate_pps", "packets_sent",
)


def summarise(runs: List[Dict]) -> Dict:
    """Aggregate repeated runs of one (experiment, mode) pair."""
    first = runs[0]
    summary = {
        "experiment": first["experiment"],
        "mode": first["mode"],
        "loss_configured": first["loss_configured"],
        "base_delay_ms": first["base_delay_ms"],
        "jitter_ms": first["jitter_ms"],
        "repeats": len(runs),
        "integrity_verified": all(r["integrity_verified"] for r in runs),
    }
    for key in METRICS:
        values = [r[key] for r in runs]
        summary[key] = round(statistics.fmean(values), 3)
        summary[f"{key}_sd"] = round(statistics.stdev(values), 3) if len(values) > 1 else 0.0
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Repeat fixed vs adaptive experiments")
    parser.add_argument("--size-kb", type=int, default=100, help="Payload size in KB")
    parser.add_argument("--repeats", type=int, default=5, help="Runs per condition and mode")
    parser.add_argument("--results-dir", default="results", help="Output directory")
    args = parser.parse_args()

    results_dir = Path(args.results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)

    rng = random.Random(1337)  # same payload for every trial
    payload = bytes(rng.getrandbits(8) for _ in range(args.size_kb * 1024))

    raw: List[Dict] = []
    summary: List[Dict] = []
    started = time.time()
    for exp in EXPERIMENTS:
        for mode in MODES:
            runs = []
            for seed in range(1, args.repeats + 1):
                trial = run_single_trial(replace(exp, random_seed=seed), mode, payload, results_dir)
                trial["seed"] = seed
                runs.append(trial)
                print(f"{exp.name} | {mode:8} | seed {seed} | {trial['goodput_kbps']} kbps | "
                      f"retx {trial['retransmissions']} | ok={trial['integrity_verified']}", flush=True)
            raw.extend(runs)
            summary.append(summarise(runs))

    out = {
        "meta": {
            "size_kb": args.size_kb,
            "repeats": args.repeats,
            "seeds": list(range(1, args.repeats + 1)),
            "payload_seed": 1337,
            "elapsed_s": round(time.time() - started, 1),
        },
        "summary": summary,
        "runs": raw,
    }
    (results_dir / "experiment_repeats.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"Wrote {results_dir / 'experiment_repeats.json'}")


if __name__ == "__main__":
    main()
