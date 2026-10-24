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
