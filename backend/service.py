"""
Adaptive UDP Streaming — Transfer Service (backend)
===================================================

Runs a full loopback transfer (receiver thread + sender) on top of the
protocol core in ``src/`` and returns structured results. The HTTP layer in
``backend/server.py`` is a thin wrapper around :func:`run_transfer`.

The protocol modules (``src.packet``, ``src.sender``, ``src.receiver``,
``src.reliability``) are used unchanged.
"""

import hashlib
import json
import random
import socket
import tempfile
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Deque, Dict, List, Optional

from src.packet import HEADER_SIZE, MAX_PAYLOAD_SIZE, VERSION
from src.receiver import receive_file
from src.reliability import MaxRetriesExceededError
from src.sender import send_file

LOOPBACK = "127.0.0.1"
MAX_UPLOAD_BYTES = 2 * 1024 * 1024
HISTORY_LIMIT = 50
RECEIVER_STARTUP_DELAY_S = 0.15
RECEIVER_TIMEOUT_S = 30.0


class TransferError(Exception):
    """Raised for invalid requests or failed transfers (message is user-safe)."""


@dataclass(frozen=True)
class TransferParams:
    """Validated, immutable transfer settings."""

    arq: str = "stop-and-wait"
    interval: float = 0.001
    max_retries: int = 5
    initial_rto: float = 1.0

    @staticmethod
    def from_mapping(raw: Dict[str, str]) -> "TransferParams":
        defaults = TransferParams()
        try:
            params = TransferParams(
                arq=raw.get("arq", defaults.arq),
                interval=float(raw.get("interval", defaults.interval)),
                max_retries=int(raw.get("max_retries", defaults.max_retries)),
                initial_rto=float(raw.get("initial_rto", defaults.initial_rto)),
            )
        except (TypeError, ValueError) as exc:
            raise TransferError(f"Invalid parameter: {exc}") from exc
        if params.arq not in ("stop-and-wait", "none"):
            raise TransferError("arq must be 'stop-and-wait' or 'none'.")
        if not 0 <= params.interval <= 1:
            raise TransferError("interval must be between 0 and 1 seconds.")
        if not 0 <= params.max_retries <= 20:
            raise TransferError("max_retries must be between 0 and 20.")
        if not 0.2 <= params.initial_rto <= 10:
            raise TransferError("initial_rto must be between 0.2 and 10 seconds.")
        return params


def protocol_info() -> Dict[str, object]:
    """Static protocol constants shown by the UI."""
    return {
        "name": "AUDP",
        "version": VERSION,
        "header_bytes": HEADER_SIZE,
        "max_payload_bytes": MAX_PAYLOAD_SIZE,
        "max_upload_bytes": MAX_UPLOAD_BYTES,
    }


@dataclass(frozen=True)
class Sample:
    """A predefined test file the UI can send without any upload.

    ``kind`` selects the generator; ``size`` is the exact byte length. Content is
    deterministic (fixed seed), so repeated runs send identical bytes.
    """

    id: str
    name: str
    description: str
    kind: str  # "text" | "log" | "pattern" | "random" | "empty"
    size: int


# Sizes are chosen to exercise distinct protocol paths: empty stream, exactly one
# packet, an exact multiple of the payload size, and larger multi-packet streams.
SAMPLES = (
    Sample("empty", "Empty file", "0 bytes · zero DATA packets, only END", "empty", 0),
    Sample("note", "Short note", "Text · fits in one packet", "text", 900),
    Sample("boundary", "Packet boundary", "Text · exactly 3 full packets", "text", MAX_PAYLOAD_SIZE * 3),
    Sample("article", "Text article", "Text · about 10 KB", "text", 10 * 1024),
    Sample("server-log", "Server log", "Log lines · about 100 KB", "log", 100 * 1024),
    Sample("pattern", "Byte pattern", "Repeating 0–255 pattern · 250 KB", "pattern", 250 * 1024),
    Sample("random", "Random binary", "Incompressible bytes · 500 KB", "random", 500 * 1024),
)
_SAMPLES_BY_ID = {s.id: s for s in SAMPLES}
_WORDS = ("packet sequence checksum window timeout acknowledgement stream jitter latency "
          "retransmit receiver sender datagram buffer socket loss delay rate adaptive").split()


def list_samples() -> List[Dict[str, object]]:
    """Public description of each sample (no file content)."""
    return [{"id": s.id, "name": s.name, "description": s.description, "size": s.size} for s in SAMPLES]


def build_sample(sample_id: str) -> tuple:
    """Return ``(filename, bytes)`` for a predefined sample.

    Raises:
        TransferError: if *sample_id* is unknown.
    """
    sample = _SAMPLES_BY_ID.get(sample_id)
    if sample is None:
        raise TransferError(f"Unknown sample: {sample_id!r}.")
    rng = random.Random(sample.id)  # string seed -> stable across runs
    if sample.kind == "empty":
        data = b""
    elif sample.kind == "random":
        data = rng.randbytes(sample.size)
    elif sample.kind == "pattern":
        data = (bytes(range(256)) * (sample.size // 256 + 1))[: sample.size]
    elif sample.kind == "log":
        lines = (f"2026-10-24T10:{i // 60 % 60:02d}:{i % 60:02d}Z INFO seq={i} rtt={rng.randint(1, 90)}ms "
                 f"loss={rng.random():.3f}\n" for i in range(sample.size // 10 + 1))
        data = "".join(lines).encode()[: sample.size]
    else:
        text = " ".join(rng.choice(_WORDS) for _ in range(sample.size // 4 + 1))
        data = text.encode()[: sample.size]
    ext = "bin" if sample.kind in ("random", "pattern", "empty") else "txt"
    return f"{sample.id}.{ext}", data


RESULTS_DIR = Path(__file__).resolve().parents[1] / "results"
# Checked in order; the first file that exists is served. The experiment scripts
# write these files, so the dashboard always shows whatever they last recorded.
RESULT_FILES = ("experiment_bottleneck.json", "experiment_tuned.json", "experiment_repeats.json")


def load_results() -> Dict[str, object]:
    """Read the most recently written experiment results file on every call.

    Of the known result files that exist, the one with the newest modification time
    wins, so the dashboard always shows whatever an experiment script recorded last.

    Returns:
        ``{"source": file name, "modified": ISO timestamp, "data": parsed JSON}``.

    Raises:
        TransferError: if no results file exists or the file is not valid JSON.
    """
    existing = [RESULTS_DIR / name for name in RESULT_FILES if (RESULTS_DIR / name).is_file()]
    if not existing:
        raise TransferError("No experiment results found. Run the experiment scripts first.")
    path = max(existing, key=lambda p: p.stat().st_mtime)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise TransferError(f"Could not read {path.name}: {exc}") from exc
    modified = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(path.stat().st_mtime))
    return {"source": path.name, "modified": modified, "data": data}


def _free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind((LOOPBACK, 0))
        return s.getsockname()[1]


class TransferHistory:
    """Thread-safe, bounded, in-memory record of completed transfers."""

    def __init__(self, limit: int = HISTORY_LIMIT) -> None:
        self._items: Deque[dict] = deque(maxlen=limit)
        self._lock = threading.Lock()

    def add(self, record: dict) -> None:
        with self._lock:
            self._items.appendleft(record)

    def list(self) -> List[dict]:
        with self._lock:
            return list(self._items)


HISTORY = TransferHistory()
# Receiver/sender share loopback ports; serialise runs to keep results clean.
_RUN_LOCK = threading.Lock()


def run_transfer(filename: str, data: bytes, params: TransferParams) -> dict:
    """Send *data* to a local receiver over UDP and report the outcome.

    Raises:
        TransferError: on invalid input or if the transfer fails.
    """
    if len(data) > MAX_UPLOAD_BYTES:
        raise TransferError(f"File too large (max {MAX_UPLOAD_BYTES} bytes).")
    safe_name = Path(filename or "upload.bin").name or "upload.bin"

    with _RUN_LOCK, tempfile.TemporaryDirectory(prefix="audp_") as tmp:
        src_path = Path(tmp) / "input.bin"
        dst_path = Path(tmp) / "output.bin"
        src_path.write_bytes(data)
        port = _free_udp_port()
        outcome: Dict[str, object] = {}

        def _receive() -> None:
            try:
                outcome["receiver"] = receive_file(
                    dst_path, LOOPBACK, port,
                    timeout=RECEIVER_TIMEOUT_S, post_end_timeout=2.0,
                    arq=params.arq, linger_timeout=0.2,
                )
            except (TimeoutError, RuntimeError, ValueError) as exc:
                outcome["receiver_error"] = str(exc)

        thread = threading.Thread(target=_receive, daemon=True)
        thread.start()
        time.sleep(RECEIVER_STARTUP_DELAY_S)

        started = time.monotonic()
        try:
            sent = send_file(
                src_path, LOOPBACK, port, params.interval,
                arq=params.arq, max_retries=params.max_retries,
                initial_rto=params.initial_rto,
            )
        except (MaxRetriesExceededError, ValueError, FileNotFoundError) as exc:
            raise TransferError(f"Sender failed: {exc}") from exc
        thread.join(timeout=10)
        elapsed = time.monotonic() - started

        if "receiver_error" in outcome:
            raise TransferError(f"Receiver failed: {outcome['receiver_error']}")
        received: Optional[dict] = outcome.get("receiver")  # type: ignore[assignment]
        if received is None:
            raise TransferError("Receiver did not finish in time.")

    source_hash = hashlib.sha256(data).hexdigest()
    record = {
        "filename": safe_name,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "params": asdict(params),
        "bytes": len(data),
        "packets": sent["total_packets"],
        "retransmissions": sent["retransmissions"],
        "duplicates": received["duplicates"],
        "final_rto_s": sent["rto"],
        "elapsed_s": round(elapsed, 4),
        "goodput_kbps": round(len(data) * 8 / 1000 / elapsed, 2) if elapsed > 0 else 0.0,
        "sha256_sent": source_hash,
        "sha256_received": received["sha256"],
        "integrity_ok": source_hash == received["sha256"],
    }
    HISTORY.add(record)
    return record
