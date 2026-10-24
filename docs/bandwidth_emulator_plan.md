# Plan: bandwidth-limited link for the network emulator

Owner of the change: Subham Kumar Sahu (emulator and rate control)
Experiment runs and analysis: Sudhanshu Aryan
Review: Nishtha Bhushan
Status: plan only. No emulator code has been changed by this document.

## 1. Why this is needed

The emulator currently drops packets at random and adds a fixed delay plus jitter. Neither depends on how fast the
sender transmits. Measured result (`results/experiment_tuned.json`):

- Under 2–10% random loss, a controller that slows down only loses goodput; slowing down does not reduce loss.
- The fixed-rate baseline (200 packets/s) is an arbitrary number. A sender that never backs off and simply sends
  faster would "win", which says nothing about adapting to a network.

Adaptive rate control only matters when sending too fast causes harm: a queue fills, delay grows, packets are
dropped. The emulator therefore needs a **bottleneck link with a finite queue**. That makes the claim testable:

> Adaptive transmission performs better and more stably than fixed-rate UDP when the available bandwidth varies.

## 2. Emulator change (src/emulator.py)

New fields on `EmulatorConfig`, all optional so existing tests and experiments behave exactly as before:

| Field | Default | Meaning |
|---|---|---|
| `bandwidth_kbps` | `0` (unlimited) | Capacity of the bottleneck in kilobits per second |
| `queue_packets` | `50` | Maximum packets waiting at the bottleneck (tail drop) |
| `bandwidth_direction` | `"forward"` | Apply the limit to DATA only; ACKs stay unconstrained |

Validation: `bandwidth_kbps >= 0`, `queue_packets >= 1`, `bandwidth_direction` in `forward / reverse / both`.

### Algorithm (inside `process_packet`, under the existing lock)

Model the link as a single server that is busy until `_link_free_at` (monotonic seconds):

```
if bandwidth_kbps == 0 or direction not limited:  -> existing behaviour
now        = time.monotonic()
service_s  = len(raw_data) * 8 / (bandwidth_kbps * 1000)
start      = max(now, _link_free_at)
waiting    = (start - now) / service_s          # packets already queued ahead
if waiting >= queue_packets:  drop, count as congestion drop
else:
    _link_free_at = start + service_s
    queue_delay_ms = (start + service_s - now) * 1000
    delay_ms = existing_delay_ms + queue_delay_ms
```

Required counters, kept separate so results can tell the two causes apart:
`total_dropped` (random, unchanged), `congestion_drops` (queue overflow), and `max_queue_delay_ms`.

Keep the random-loss seed behaviour unchanged: draw the random number first and exactly as today so that
seeded runs without a bandwidth limit stay identical.

## 3. Tests to write first (tests/test_emulator.py)

1. `bandwidth_kbps=0` leaves delay and drops identical to the current behaviour (seeded comparison).
2. One packet of N bytes on an idle link gets delay equal to `N*8/(kbps*1000)` plus the base delay.
3. A burst of K packets sent at once: the i-th packet waits `i * service_s`.
4. A burst larger than `queue_packets` produces congestion drops, and only the overflow is dropped.
5. Sending at a rate below capacity never queues and never drops.
6. The same seed and the same arrivals give the same result.
7. ACKs (reverse direction) are not rate-limited when `bandwidth_direction="forward"`.
8. Congestion drops and random drops are counted separately.

## 4. Experiment design (decided before running, so results cannot steer it)

Fixed settings for every run: 100 KB payload, base delay 20 ms, jitter 5 ms, queue 50 packets, no random loss,
seeds 1–5, same seeds in every mode. One 1452-byte packet is about 11.6 kbit.

| ID | Bottleneck | Approx. capacity |
|---|---|---|
| B1 | 5000 kbps | 430 packets/s, above the 200 pps fixed rate |
| B2 | 2000 kbps | 170 packets/s |
| B3 | 1000 kbps | 86 packets/s |
| B4 | 500 kbps | 43 packets/s |

Modes compared in each condition:

- **Fixed** at 200 packets/s (the existing baseline).
- **Fixed sweep** at 25, 50, 100, 200 and 400 packets/s. This answers "what if the fixed rate had been chosen
  better?". The point of adaptation is that nobody knows the right fixed rate in advance.
- **Adaptive**, default controller settings. If it needs tuning, tune on seeds 101–103 with the existing grid in
  `experiments/run_tuned.py` and report on seeds 1–5.

Metrics per run: goodput, average RTT (shows queueing delay), retransmissions, congestion drops, integrity check.
Stability is the spread of goodput across the five seeds.

### What counts as supporting the claim (fixed in advance)

The claim is supported for a condition if the adaptive controller, against the 200 pps fixed baseline:

1. has goodput at least as high, **or** at least 90% of the best fixed rate in the sweep; and
2. has lower average RTT; and
3. has no more retransmissions.

It is supported overall only if this holds in at least three of four conditions. If it does not, the result is
reported as it is and the analysis explains why. The tuning grid and every default-vs-tuned result stay visible on
the dashboard.

## 5. How the results reach the dashboard

`experiments/run_bottleneck.py` is already written. It refuses to run until the emulator accepts `bandwidth_kbps`,
then writes `results/experiment_bottleneck.json` in the same shape the dashboard already reads. The dashboard picks
the newest results file on disk, so no front-end change is needed after the run:

```
python -m experiments.run_bottleneck --size-kb 100 --repeats 5
```

## 6. Order of work

1. Subham: write the tests in section 3 (they fail), then implement the change, then make them pass.
2. Nishtha: review the change and the tests.
3. Sudhanshu: run the experiment script, then write the analysis from the measured numbers.
4. Rate-controller tuning, only if step 3 shows adaptive falling short, using the held-out method above.

The emulator and rate controller are core protocol code, so they should be written and understood by their
owners. Any AI assistance in them must be recorded in `AI-USE.md`.
