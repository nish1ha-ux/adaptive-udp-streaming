# Review of the emulator, rate controller and paced sender

Reviewed for merge into `development`. Code by Subham Kumar Sahu (`feature/rate-control-and-emulator`). Reviewer:
Nishtha Bhushan, with AI assistance (see `AI-USE.md`).

## Decision: accepted, added to the project scope

These modules are required deliverables in the proposal (network emulator for loss, delay and jitter; adaptive
transmission-rate control; fixed-rate baseline for comparison). They were merged, not rewritten, and all of the
branch's tests pass alongside the existing suite.

## What was added

| File | Purpose |
|---|---|
| `src/emulator.py` | `NetworkEmulator`, `EmulatorConfig`, `UDPRelayServer`: seeded random loss, base delay and jitter, applied per direction; a UDP relay that sits between sender and receiver |
| `src/rate_control.py` | `AdaptiveRateController` and `RateControllerConfig`: AIMD rate control with fixed and adaptive modes |
| `src/paced_sender.py` | The branch's sender (rate-paced, asynchronous ACKs, per-packet retransmission, statistics). Its function is `send_file`. |
| `experiments/run_experiments.py` | First fixed-vs-adaptive experiment suite (E1 to E4) |
| `tests/test_emulator.py`, `test_rate_control.py`, `test_integration.py` | Unit and end-to-end tests |
| `docs/architecture_and_rate_control.md` | The author's architecture note |

## How it was integrated

The branch's version of `sender.py`, `receiver.py` and `packet.py` conflicted with the stop-and-wait versions already on
the main line. Both designs are useful, so neither was discarded:

- `src/sender.py`, `src/receiver.py`, `src/packet.py` keep the stop-and-wait behaviour.
- The branch's sender lives on as `src/paced_sender.py`, unchanged apart from its location.
- The receiver gained a `selective` ACK mode (and a `send_ack` switch) so the paced sender gets an ACK for each packet.
  Stop-and-wait ACKs a packet ahead of the expected one with the previous sequence number, which a paced sender with
  many packets in flight cannot use.
- The branch's tests and experiments import `send_file` from `src.paced_sender`.

## Findings

Useful as they are:

- Seeded loss, delay and jitter make runs reproducible.
- `UDPRelayServer` needs no change to the sender or receiver; both talk to the relay as if it were the peer.
- The controller keeps fixed and adaptive modes in one class, so a comparison changes one setting.

Things to know, none of which blocked the merge:

1. **Delay applies to ACKs too.** With `direction="both"` the configured delay is added in each direction, so the
   measured RTT is about twice the base delay plus jitter.
2. **Absolute RTT threshold.** The default controller treats an RTT above 80 ms as congestion. With 20 to 100 ms of
   deliberate delay, it backs off even when sending slower cannot help. Measured result: it ran well below the fixed
   rate until its thresholds were tuned (`results/experiment_tuned.json`).
3. **No bandwidth limit.** Random loss does not depend on the sending rate, so adaptive control has nothing to gain.
   `experiments/bottleneck_link.py` adds a bandwidth-limited queue on top of the emulator for a fair test
   (`docs/bandwidth_emulator_plan.md`).
4. **Memory.** `NetworkEmulator.action_history` stores every packet decision and is never trimmed. Fine for a 100 KB
   experiment, but it would grow without bound on a long run.
5. **Failed transfers still report goodput.** When the retransmission limit is reached, the file is incomplete but a
   goodput figure is still computed. Always read it together with the integrity check.

## Verdict

Productive. Kept in scope, with findings 2 and 3 handled by experiments rather than by changing the protocol code.
