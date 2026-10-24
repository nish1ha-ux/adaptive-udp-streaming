# AI-USE Disclosure — dashboard and local server work

Course: CS-30003 · Coding Assignment 1 · Path B
Repository: https://github.com/nish1ha-ux/adaptive-udp-streaming
Branch covered by this entry: `overhaul/dashboard`

This file covers the work added on this branch. Earlier AI use by team members on their own
modules is disclosed in their own entries (for example Nishtha's entry on
`feature/nishtha-udp-packet`) and is not repeated or changed here.

## Who did what

| Activity | Done by |
|---|---|
| Decision to add a dashboard, a credits page and a local test server | Nishtha Bhushan |
| Requirements: responsive on phone/tablet/desktop, no template look, full comments, no tool branding in the UI | Nishtha Bhushan |
| Decision to show only measured results, never invented ones | Nishtha Bhushan (AI refused to fabricate data; user confirmed) |
| Decision to use the teammates' existing result files and leave their protocol code untouched | Nishtha Bhushan, on the AI's recommendation |
| Choice of working branch and the plan to test locally before any merge or deploy | Nishtha Bhushan |
| Technical drafting: structure, visual design, CSS, JavaScript modules, Python server and service layer, tests for the server | AI assistant (Claude), written to the user's instructions |
| Review of the drafted code, running it locally and requesting changes | Nishtha Bhushan |

The AI also proposed the overall layout (left rail / bottom tabs), the colour system and the
verdict logic on the dashboard; those proposals were reviewed and accepted by the user.
Accepting a proposal is recorded here as a user decision, but the original suggestion came from the AI.

## What the AI drafted

- `backend/service.py`, `backend/server.py`: loopback transfer service and HTTP API (standard library only).
- `frontend/`: `index.html`, `styles.css`, `js/app.js`, `js/data.js`, `js/charts.js`.
- `frontend/data/team.json`: credits text, compiled from the proposal and the repository history.
- `tests/test_backend.py`: tests for the service and API.
- Predefined sample files (`SAMPLES` in `backend/service.py`) so a transfer needs no upload; requested by Nishtha.
- `experiments/run_repeats.py`: wrapper that repeats Subham's existing trial runner with seeds 1–5 and reports
  mean and standard deviation. It calls his unmodified `run_single_trial`; it adds no measurement logic.
- `experiments/run_tuned.py`: searches the rate controller's own threshold settings on tuning seeds (101-103) and re-measures the chosen setting on held-out seeds (1-5). It edits no protocol code, and every grid point is saved in `results/experiment_tuned.json`.
- `docs/bandwidth_emulator_plan.md`: written plan for a bandwidth-limited emulator link, with tests and a success rule fixed before any run. It is a plan only; the emulator change is for its owner to write.
- `experiments/bottleneck_link.py` and `tests/test_bottleneck_link.py`: a bandwidth-limited queue implemented as subclasses of the emulator classes, so `src/emulator.py` is unchanged. Written by the AI at Nishtha's instruction to proceed without waiting for the emulator's owner; it is test infrastructure outside `src/`, and its owner should review it.
- `experiments/run_bottleneck.py`: experiment runner for that link.
- README sections describing the above.

## What the AI did not write

- The core protocol: `src/packet.py`, `src/sender.py`, `src/receiver.py`, `src/reliability.py`.
- The emulator, rate controller and single-trial runner (Subham's work, on `feature/rate-control-and-emulator`).
  The result files in `results/` were produced by running that code through the scripts above; the dashboard
  reads them from disk and does not contain any numbers of its own.

## Notes on the data

`results/experiment_repeats.json` was produced by running `experiments/run_repeats.py` against the emulator and
rate-controller code on `feature/rate-control-and-emulator` (100 KB payload, 5 seeded repeats per condition and mode,
all SHA-256 checks passing). The dashboard computes its verdict from these numbers. In this run adaptive goodput was
higher only on the ideal network and clearly lower under impairment, and the dashboard reports that as measured.
The request to make the claim "get supported" was not implemented by changing results; improving the result would
mean changing the rate controller, which belongs to its author and is not core code this entry covers.

## Viva readiness

The web layer is not core protocol code, but the author is expected to be able to explain and modify it live.
