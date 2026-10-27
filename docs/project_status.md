# Project status and remaining work

Snapshot taken 24 October 2026. The percentages are rough estimates made from the repository files, the commit
history and the experiment results. They are not measurements.

## Progress against each member's part of the proposal

| Member | Part (from the proposal) | Done | Evidence | Left |
|---|---|---|---|---|
| Nishtha Bhushan | UDP communication and packet format | ~95% | Packet format, checksum, sender, receiver, reconstruction, SHA-256 checks and tests are on `main` | `docs/protocol_design.md`, viva preparation |
| Prakhar Manish Srivastava | Acknowledgements, retransmission, RTT | ~85% | ACK packet, RTT estimator, Stop-and-Wait retransmission and tests are on `main` | Jitter measurement on `main`; conflicts with the emulator branch's sender and receiver |
| Subham Kumar Sahu | Adaptive rate control and emulator | ~75–80% | Emulator, adaptive controller, fixed and adaptive modes, tests and a first experiment suite on `feature/rate-control-and-emulator` | Merge into `main`; default controller underperforms in measurements; no bandwidth limit in the emulator itself |
| Sudhanshu Aryan | Testing, experiments and analysis | ~0–10% | No commits yet. The experiment scripts and results come from the shared work on this branch | Graphs, written analysis, final report, integration tests |

The dashboard and local test server are supporting tools and are not counted toward anyone's share.

## Whole project: about 70–75% complete

| Area | Estimate |
|---|---|
| Protocol core and reliability | ~100% (on `main`) |
| Emulator and adaptive rate control | ~80% (built, not merged) |
| Experiments and evidence | ~60% (runs and tuning done; claim only partly supported) |
| Dashboard and tooling | ~90% |
| Report, documentation and viva readiness | ~40% |

## What the measurements say so far

On the bandwidth-limited link with 100 KB transfers, the tuned adaptive controller had no retransmissions and about
half the delay on the congested links, and it delivered the file where fixed-rate 200 packets/s overran the queue. It
sends fewer bits per second than the fixed-rate baseline on these short transfers. The report has to state this as
measured. See `results/experiment_bottleneck_tuned.json` and the dashboard.

## Before submission

1. Merge `feature/rate-control-and-emulator` into `main`. Resolve the conflicts in `src/sender.py`, `src/receiver.py`
   and `src/packet.py` (Prakhar and Subham), then run the full test suite.
2. Run longer transfers (1 MB or more, same seeds), because a 100 KB file hides the benefit of adapting.
3. Decide how the report states the claim, based on the measured data.
4. Sudhanshu: graphs (matplotlib, as in the proposal), written analysis and final report.
5. Make `AI-USE.md` cover the whole team: Path B instead of P3, and an honest entry for each member.
6. Settle the commit-authorship question for Prakhar's three commits. He has to confirm it in writing, and the
   instructor should be told.
7. Add the jitter measurement to `main` and write `docs/protocol_design.md`.
8. Tidy the repository, open the pull request into `main`, check `requirements.txt`, add Sudhanshu's GitHub handle to
   the Credits page.
9. Viva preparation: every member must be able to explain and edit their own code live.
10. Optional: Netlify deployment (the results file has to be copied into the build).
