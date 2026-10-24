"""
Adaptive UDP Streaming — Packet Format (v1)
============================================

Author: Nishtha Bhushan
Module: src/packet.py

Binary wire format (network byte order, big-endian):

  Offset  Size  Field
  ------  ----  -----
   0       4    Magic bytes  ("AUDP")
   4       1    Protocol version (1)
   5       1    Packet type  (1 = DATA, 2 = END, 3 = ACK)
   6       4    Sequence number (uint32)
  10       8    Timestamp in nanoseconds (uint64, monotonic clock)
  18       2    Payload length (uint16, 0–MAX_PAYLOAD_SIZE)
  20      32    SHA-256 checksum of (header fields + payload)
  52       N    Payload bytes (N = payload length)

Total header size: 52 bytes.
Maximum payload:   1400 bytes (configurable via MAX_PAYLOAD_SIZE).
Maximum datagram:  1452 bytes.

Packet types
------------
DATA (1): Carries a chunk of the file being streamed.
          sequence_number is the zero-based chunk index.
          payload contains the file chunk (1–MAX_PAYLOAD_SIZE bytes,
          or 0 bytes only when the source file is empty).

END  (2): Signals the end of the stream.
          sequence_number equals the total number of DATA packets sent.
          payload encodes the total DATA packet count as a 4-byte
          big-endian unsigned integer, enabling the receiver to verify
          completeness without requiring a separate out-of-band channel.

ACK  (3): Acknowledges receipt of a packet.
          sequence_number specifies the acknowledged sequence number
          (matching the DATA chunk index or END sequence number).
          timestamp_ns echoes or records the timestamp for RTT calculation.
          payload is empty (0 bytes) in this version.

Checksum
--------
The SHA-256 checksum covers the concatenation of the header fields
(magic, version, type, sequence number, timestamp, payload length)
packed in network byte order, followed by the raw payload bytes.
The checksum is computed *before* it is placed into the header and
is validated on decode using ``hmac.compare_digest`` to resist
timing attacks.

Exceptions
----------
All validation failures raise ``PacketError`` (a ``ValueError``
subclass) with a descriptive message.
"""

import hashlib
import hmac
import struct
import time
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Protocol constants
# ---------------------------------------------------------------------------

MAGIC: bytes = b"AUDP"
"""4-byte magic identifier at the start of every datagram."""

VERSION: int = 1
"""Protocol version.  Incremented on incompatible format changes."""

TYPE_DATA: int = 1
"""Packet type for file-data chunks."""

TYPE_END: int = 2
"""Packet type for the end-of-stream marker."""

TYPE_ACK: int = 3
"""Packet type for acknowledgments."""

_VALID_TYPES = frozenset({TYPE_DATA, TYPE_END, TYPE_ACK})

# struct format: magic(4s) version(B) type(B) seq(I) ts(Q) len(H) sha256(32s)
HEADER_FORMAT: str = "!4sBBIQH32s"
HEADER_SIZE: int = struct.calcsize(HEADER_FORMAT)

MAX_PAYLOAD_SIZE: int = 1400
"""
Maximum payload per DATA packet, in bytes.

Chosen to fit within a typical Ethernet MTU (1500) after accounting
for IP (20 B) and UDP (8 B) headers, with some margin.
"""

MAX_DATAGRAM_SIZE: int = HEADER_SIZE + MAX_PAYLOAD_SIZE
"""Maximum size of a complete on-wire datagram."""

# Format used for the total-packet count encoded in END payloads.
_END_PAYLOAD_FORMAT: str = "!I"
_END_PAYLOAD_SIZE: int = struct.calcsize(_END_PAYLOAD_FORMAT)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class PacketError(ValueError):
    """Raised when a packet is malformed, truncated, oversized or
    otherwise invalid."""


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------

@dataclass
class Packet:
    """Decoded representation of an AUDP datagram.

    Attributes:
        packet_type:     TYPE_DATA or TYPE_END.
        sequence_number: Zero-based chunk index (DATA) or total DATA
                         packet count (END).
        timestamp_ns:    Monotonic timestamp in nanoseconds.
        payload:         Raw payload bytes.
    """
    packet_type: int
    sequence_number: int
    timestamp_ns: int
    payload: bytes


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _validate_fields(packet_type, sequence_number, timestamp_ns, payload):
    """Validate packet fields and raise ``PacketError`` on any problem."""

    if packet_type not in _VALID_TYPES:
        raise PacketError(
            f"Unsupported packet type: {packet_type!r}. "
            f"Valid types are DATA ({TYPE_DATA}), END ({TYPE_END}), and ACK ({TYPE_ACK})."
        )

    if not isinstance(sequence_number, int) or not 0 <= sequence_number <= 0xFFFFFFFF:
        raise PacketError(
            f"Invalid sequence number: {sequence_number!r}. "
            "Must be an integer in [0, 2^32 - 1]."
        )

    if not isinstance(timestamp_ns, int) or not 0 <= timestamp_ns <= 0xFFFFFFFFFFFFFFFF:
        raise PacketError(
            f"Invalid timestamp: {timestamp_ns!r}. "
            "Must be an integer in [0, 2^64 - 1]."
        )

    if not isinstance(payload, (bytes, bytearray)):
        raise PacketError(
            f"Payload must be bytes, got {type(payload).__name__}."
        )

    if packet_type == TYPE_ACK and len(payload) != 0:
        raise PacketError(
            f"ACK packet payload must be empty, got {len(payload)} bytes."
        )

    if len(payload) > MAX_PAYLOAD_SIZE:
        raise PacketError(
            f"Payload too large: {len(payload)} bytes "
            f"(maximum {MAX_PAYLOAD_SIZE})."
        )


def _checksum_input(packet_type, sequence_number, timestamp_ns, payload):
    """Return the byte string over which the SHA-256 checksum is computed."""
    return struct.pack(
        "!4sBBIQH",
        MAGIC,
        VERSION,
        packet_type,
        sequence_number,
        timestamp_ns,
        len(payload),
    ) + payload


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_ack_packet(
    sequence_number,
    timestamp_ns=None,
):
    """Create a validated ``Packet`` of type ``TYPE_ACK`` with empty payload.

    Parameters:
        sequence_number: Acknowledged sequence number (chunk index or END count).
        timestamp_ns:    Timestamp in nanoseconds; auto-generated if *None*.

    Returns:
        A ``Packet`` instance.

    Raises:
        PacketError: If *sequence_number* or *timestamp_ns* is invalid.
    """
    return create_packet(
        packet_type=TYPE_ACK,
        sequence_number=sequence_number,
        payload=b"",
        timestamp_ns=timestamp_ns,
    )


def create_packet(
    packet_type,
    sequence_number,
    payload=b"",
    timestamp_ns=None,
):
    """Create a validated ``Packet`` without encoding it.

    Parameters:
        packet_type:     TYPE_DATA or TYPE_END.
        sequence_number: Chunk index (DATA) or total count (END).
        payload:         Raw payload bytes (default empty).
        timestamp_ns:    Monotonic timestamp; auto-generated if *None*.

    Returns:
        A ``Packet`` instance.

    Raises:
        PacketError: If any field is invalid.
    """
    if timestamp_ns is None:
        timestamp_ns = time.monotonic_ns()

    _validate_fields(packet_type, sequence_number, timestamp_ns, payload)

    return Packet(
        packet_type=packet_type,
        sequence_number=sequence_number,
        timestamp_ns=timestamp_ns,
        payload=bytes(payload),
    )


def encode_packet(packet):
    """Encode a ``Packet`` into an on-wire datagram (bytes).

    The SHA-256 checksum is computed over the header fields and payload
    and embedded in the header before the payload is appended.

    Returns:
        ``bytes`` — the complete datagram ready for ``socket.sendto``.

    Raises:
        PacketError: If the packet fields are invalid.
    """
    _validate_fields(
        packet.packet_type,
        packet.sequence_number,
        packet.timestamp_ns,
        packet.payload,
    )

    checksum_data = _checksum_input(
        packet.packet_type,
        packet.sequence_number,
        packet.timestamp_ns,
        packet.payload,
    )
    checksum = hashlib.sha256(checksum_data).digest()

    header = struct.pack(
        HEADER_FORMAT,
        MAGIC,
        VERSION,
        packet.packet_type,
        packet.sequence_number,
        packet.timestamp_ns,
        len(packet.payload),
        checksum,
    )

    return header + packet.payload


def decode_packet(data):
    """Decode a raw datagram into a ``Packet``.

    Validates the magic bytes, protocol version, payload length,
    and SHA-256 checksum.

    Parameters:
        data: Raw datagram bytes as received from the socket.

    Returns:
        A ``Packet`` instance.

    Raises:
        PacketError: If the datagram is truncated, malformed, has an
            unsupported version, bad checksum, or invalid fields.
    """
    if not isinstance(data, (bytes, bytearray)):
        raise PacketError("Datagram must be bytes.")

    if len(data) < HEADER_SIZE:
        raise PacketError(
            f"Datagram too short: {len(data)} bytes "
            f"(minimum header size is {HEADER_SIZE})."
        )

    (
        magic,
        version,
        packet_type,
        sequence_number,
        timestamp_ns,
        payload_length,
        received_checksum,
    ) = struct.unpack(HEADER_FORMAT, data[:HEADER_SIZE])

    if magic != MAGIC:
        raise PacketError(
            f"Invalid protocol identifier: {magic!r} (expected {MAGIC!r})."
        )

    if version != VERSION:
        raise PacketError(
            f"Unsupported protocol version: {version} "
            f"(supported: {VERSION})."
        )

    if packet_type not in _VALID_TYPES:
        raise PacketError(
            f"Unsupported packet type: {packet_type}."
        )

    if packet_type == TYPE_ACK and payload_length != 0:
        raise PacketError(
            f"ACK datagram payload length must be 0, got {payload_length}."
        )

    if payload_length > MAX_PAYLOAD_SIZE:
        raise PacketError(
            f"Payload length in header ({payload_length}) exceeds "
            f"maximum ({MAX_PAYLOAD_SIZE})."
        )

    expected_total = HEADER_SIZE + payload_length
    if len(data) != expected_total:
        raise PacketError(
            f"Datagram size mismatch: got {len(data)} bytes, "
            f"expected {expected_total} (header {HEADER_SIZE} + "
            f"payload {payload_length})."
        )

    payload = data[HEADER_SIZE:]

    # Verify checksum.
    checksum_data = _checksum_input(
        packet_type, sequence_number, timestamp_ns, payload,
    )
    expected_checksum = hashlib.sha256(checksum_data).digest()

    if not hmac.compare_digest(received_checksum, expected_checksum):
        raise PacketError("Checksum verification failed.")

    return Packet(
        packet_type=packet_type,
        sequence_number=sequence_number,
        timestamp_ns=timestamp_ns,
        payload=bytes(payload),
    )


def verify_checksum(data):
    """Return ``True`` if *data* is a valid datagram with correct checksum.

    This is a convenience wrapper around ``decode_packet`` that returns
    a boolean instead of raising.
    """
    try:
        decode_packet(data)
        return True
    except (PacketError, struct.error):
        return False


def encode_end_payload(total_data_packets):
    """Encode the total DATA packet count into an END-packet payload.

    Parameters:
        total_data_packets: Number of DATA packets in the stream (uint32).

    Returns:
        ``bytes`` — 4-byte big-endian unsigned integer.

    Raises:
        PacketError: If the count is out of range.
    """
    if not isinstance(total_data_packets, int) or not 0 <= total_data_packets <= 0xFFFFFFFF:
        raise PacketError(
            f"Total DATA packet count out of range: {total_data_packets!r}."
        )
    return struct.pack(_END_PAYLOAD_FORMAT, total_data_packets)


def decode_end_payload(payload):
    """Decode the total DATA packet count from an END-packet payload.

    Parameters:
        payload: The raw payload bytes from a decoded END packet.

    Returns:
        ``int`` — total number of DATA packets in the stream.

    Raises:
        PacketError: If the payload size is incorrect.
    """
    if len(payload) != _END_PAYLOAD_SIZE:
        raise PacketError(
            f"END payload size mismatch: got {len(payload)} bytes, "
            f"expected {_END_PAYLOAD_SIZE}."
        )
    (count,) = struct.unpack(_END_PAYLOAD_FORMAT, payload)
    return count
