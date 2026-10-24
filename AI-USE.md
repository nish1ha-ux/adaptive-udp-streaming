# AI-USE Disclosure

Course: CS-30003 · Coding Assignment 1 · Path B (own proposal)
Project: Adaptive Transport Protocol for Real-Time Streaming over UDP
Repository: https://github.com/nish1ha-ux/adaptive-udp-streaming

## 1. Course policy

- Permitted: explaining concepts, debugging, reviewing code, generating tests, boilerplate, plotting scripts, improving writing.
- Not permitted: generating the core protocol implementation and submitting it as one's own work.
- Required: this file, recording what AI was used for. Honest disclosure carries no penalty; undisclosed use found at
  the viva does.
- Every member must be able to explain and modify their own code live at the individual viva.

Each member records their own AI use in their own section below.

## 2. Nishtha Bhushan — UDP communication, packet format, repository

### 2.1 Earlier work (packet format, sender, receiver)

Tools: Google Antigravity IDE assistant, OpenAI ChatGPT. This section carries over her earlier disclosure, corrected to
the project's track (Path B).

| Category | Files | AI assistance |
|---|---|---|
| Core protocol | `src/packet.py`, `src/sender.py`, `src/receiver.py` | ChatGPT for design discussion on UDP framing and binary protocol structure. Antigravity assisted with drafting the 52-byte header (`!4sBBIQH32s`), SHA-256 checksum with constant-time comparison, socket send/receive loops, pacing and ACK packet support. |
| Tests | `tests/test_packet.py`, `tests/test_udp_transfer.py` | Antigravity assisted with generating unit and integration tests. |
| Documentation | `README.md` | ChatGPT for README structure; Antigravity for formatting protocol tables, usage and diagrams. |

### 2.2 Dashboard, server, experiments, integration, CI (this branch of work)

Claude (an AI assistant) wrote the code below at Nishtha's instruction, in a conversation where she made the decisions
and reviewed the results. The split is:

| Activity | Done by |
|---|---|
| Decisions: add a dashboard, credits page and local server; responsive on phone, tablet and desktop; no tool branding in the UI; show only measured results; use predefined sample files; read live data from disk; show local loopback runs on the Overview | Nishtha Bhushan |
| Decisions: tune the controller's own settings rather than edit protocol code; add a bandwidth-limited test link; proceed without waiting for the emulator's owner; branching model with an automatic `development` branch and a manual `main` | Nishtha Bhushan |
| Suggestions that were accepted: overall layout and colour system, held-out tuning method, the structure of the bandwidth-limited link, how to reconcile the two sender designs | AI assistant (accepted by Nishtha) |
| Writing the code and documents listed below | AI assistant |
| Running everything locally, reading the results, reviewing, requesting changes, approving the merge | Nishtha Bhushan |

Drafted by the AI assistant:

- `backend/` (loopback transfer service, sample files, results loader, persisted run log, JSON API; standard library only).
- `frontend/` (dashboard pages, styles, scripts) and `frontend/data/team.json` (credits text from the proposal and history).
- `experiments/run_repeats.py`, `run_tuned.py`, `run_bottleneck.py`, `run_bottleneck_tuned.py`, `bottleneck_link.py`:
  experiment runners and the bandwidth-limited queue, built on top of the emulator classes without editing them.
  Controller tuning uses seeds 101–103 and reports on separate seeds 1–5; every configuration tried is stored.
- The integration of the emulator branch: moving Subham's sender to `src/paced_sender.py` and adding a `selective`
  ACK mode and `send_ack` switch to `src/receiver.py` (about 25 lines). **This touches core protocol code.** It was
  done at Nishtha's instruction to combine the two sender designs; Prakhar (receiver, ARQ) and Subham (paced sender)
  should review it.
- Tests: `tests/test_backend.py`, `tests/test_bottleneck_link.py`, `tests/test_repo_health.py`.
- Automation: `.github/workflows/` (tests, automatic merge into `development`, manual promotion to `main`).
- Documents: `docs/protocol_design.md` (drafted from the code and to be reviewed by Nishtha as the packet-format owner),
  `docs/emulator_review.md`, `docs/bandwidth_emulator_plan.md`, `docs/project_status.md`, README updates, this file.

Not written by the AI: the emulator, rate controller and single-trial runner (Subham); the stop-and-wait ARQ,
RTT estimator and their tests (Prakhar). Experiment numbers come from running that code; the dashboard reads them from
`results/` and contains no figures of its own. A request to make the results support the claim was not carried out
by altering results.

## 3. Prakhar Manish Srivastava — ACK, retransmission, RTT

His entry belongs here, written by him (`src/reliability.py`, the ARQ integration in `src/sender.py` and
`src/receiver.py`, and `tests/test_reliability.py`, `tests/test_arq_transfer.py`).

## 4. Subham Kumar Sahu — adaptive rate control and emulator

His entry belongs here, written by him (`src/emulator.py`, `src/rate_control.py`, `src/paced_sender.py`,
`experiments/run_experiments.py`, and the emulator, rate-control and integration tests).

## 5. Sudhanshu Aryan — testing, experiments and analysis

His entry belongs here, written by him.

## 6. Viva readiness

Anyone presenting a part must be able to explain and edit it live, including parts that were AI-drafted.
