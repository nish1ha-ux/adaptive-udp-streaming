
import hashlib
import hmac
import struct
import time
from dataclasses import dataclass

MAGIC = b"AUDP"
VERSION = 1

TYPE_DATA = 1
TYPE_END = 2
TYPE_ACK = 3

HEADER_FORMAT = "!4sBBIQH32s"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)
MAX_PAYLOAD_SIZE = 1400


class PacketError(ValueError):
    """Raised when a packet is invalid."""


@dataclass
class Packet:
    packet_type: int
    sequence_number: int
    timestamp_ns: int
    payload: bytes


def _checksum_prefix(packet_type, sequence_number, timestamp_ns, payload):
    if packet_type not in (TYPE_DATA, TYPE_END, TYPE_ACK):
        raise PacketError("Unsupported packet type")

    if not 0 <= sequence_number <= 0xFFFFFFFF:
        raise PacketError("Invalid sequence number")

    if not 0 <= timestamp_ns <= 0xFFFFFFFFFFFFFFFF:
        raise PacketError("Invalid timestamp")

    if len(payload) > MAX_PAYLOAD_SIZE:
        raise PacketError("Payload too large")

    if packet_type == TYPE_END and payload:
        raise PacketError("END packet must have empty payload")

    return struct.pack(
        "!4sBBIQH",
        MAGIC,
        VERSION,
        packet_type,
        sequence_number,
        timestamp_ns,
        len(payload),
    ) + payload


def create_packet(
    packet_type,
    sequence_number,
    payload=b"",
    timestamp_ns=None,
):
    if not isinstance(payload, bytes):
        raise PacketError("Payload must be bytes")

    if timestamp_ns is None:
        timestamp_ns = time.monotonic_ns()

    _checksum_prefix(
        packet_type, sequence_number, timestamp_ns, payload
    )

    return Packet(
        packet_type,
        sequence_number,
        timestamp_ns,
        payload,
    )


def encode_packet(packet):
    prefix = _checksum_prefix(
        packet.packet_type,
        packet.sequence_number,
        packet.timestamp_ns,
        packet.payload,
    )
    checksum = hashlib.sha256(prefix).digest()

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
    if len(data) < HEADER_SIZE:
        raise PacketError("Datagram shorter than header")

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
        raise PacketError("Invalid protocol identifier")

    if version != VERSION:
        raise PacketError("Unsupported protocol version")

    if payload_length > MAX_PAYLOAD_SIZE:
        raise PacketError("Payload too large")

    if len(data) != HEADER_SIZE + payload_length:
        raise PacketError("Incorrect payload length")

    payload = data[HEADER_SIZE:]

    prefix = _checksum_prefix(
        packet_type,
        sequence_number,
        timestamp_ns,
        payload,
    )
    expected_checksum = hashlib.sha256(prefix).digest()

    if not hmac.compare_digest(received_checksum, expected_checksum):
        raise PacketError("Checksum verification failed")

    return Packet(
        packet_type,
        sequence_number,
        timestamp_ns,
        payload,
    )


def verify_checksum(data):
    try:
        decode_packet(data)
        return True
    except (PacketError, struct.error):
        return False

