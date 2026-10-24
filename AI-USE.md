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
- README sections describing the above.

## What the AI did not write

- The core protocol: `src/packet.py`, `src/sender.py`, `src/receiver.py`, `src/reliability.py`.
- The emulator, rate controller and experiment results (Subham's work). `frontend/data/results.json` is a
  copy of `results/experiment_results.json` from `feature/rate-control-and-emulator`, unmodified.

## Notes on the data

The dashboard computes a plain verdict from the measured results. At the time of writing the data is a single
22-packet run per condition, which is too small to support conclusions; the dashboard says so.

## Viva readiness

The web layer is not core protocol code, but the author is expected to be able to explain and modify it live.
