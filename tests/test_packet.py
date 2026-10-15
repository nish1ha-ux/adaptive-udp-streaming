"""
Tests for src/packet.py — packet format, encoding, decoding, checksums.

Author: Nishtha Bhushan
"""

import struct
import time

import pytest

from src.packet import (
    HEADER_FORMAT,
    HEADER_SIZE,
    MAGIC,
    MAX_PAYLOAD_SIZE,
    TYPE_DATA,
    TYPE_END,
    VERSION,
    Packet,
    PacketError,
    create_packet,
    decode_end_payload,
    decode_packet,
    encode_end_payload,
    encode_packet,
    verify_checksum,
)


# ===================================================================
# Round-trip tests
# ===================================================================

class TestRoundTrip:
    """Encode → decode should preserve all fields."""

    def test_data_packet_round_trip(self):
        original = create_packet(TYPE_DATA, 0, b"Hello UDP")
        decoded = decode_packet(encode_packet(original))

        assert decoded.packet_type == TYPE_DATA
        assert decoded.sequence_number == 0
        assert decoded.payload == b"Hello UDP"
        assert decoded.timestamp_ns == original.timestamp_ns

    def test_end_packet_round_trip(self):
        payload = encode_end_payload(42)
        original = create_packet(TYPE_END, 42, payload)
        decoded = decode_packet(encode_packet(original))

        assert decoded.packet_type == TYPE_END
        assert decoded.sequence_number == 42
        assert decode_end_payload(decoded.payload) == 42

    def test_sequence_number_preserved(self):
        for seq in (0, 1, 255, 65535, 0xFFFFFFFF):
            pkt = create_packet(TYPE_DATA, seq, b"seq")
            decoded = decode_packet(encode_packet(pkt))
            assert decoded.sequence_number == seq

    def test_payload_content_preserved(self):
        payload = bytes(range(256))
        pkt = create_packet(TYPE_DATA, 0, payload)
        decoded = decode_packet(encode_packet(pkt))
        assert decoded.payload == payload

    def test_max_payload_round_trip(self):
        payload = b"\xAB" * MAX_PAYLOAD_SIZE
        pkt = create_packet(TYPE_DATA, 0, payload)
        decoded = decode_packet(encode_packet(pkt))
        assert decoded.payload == payload
        assert len(decoded.payload) == MAX_PAYLOAD_SIZE

    def test_empty_payload_data_packet(self):
        """An empty DATA payload is valid (e.g., for an empty file)."""
        pkt = create_packet(TYPE_DATA, 0, b"")
        decoded = decode_packet(encode_packet(pkt))
        assert decoded.payload == b""

    def test_timestamp_is_monotonic(self):
        p1 = create_packet(TYPE_DATA, 0, b"a")
        p2 = create_packet(TYPE_DATA, 1, b"b")
        assert p2.timestamp_ns >= p1.timestamp_ns

    def test_explicit_timestamp(self):
        ts = 123456789
        pkt = create_packet(TYPE_DATA, 0, b"ts", timestamp_ns=ts)
        decoded = decode_packet(encode_packet(pkt))
        assert decoded.timestamp_ns == ts


# ===================================================================
# Checksum validation
# ===================================================================

class TestChecksum:
    def test_valid_checksum(self):
        encoded = encode_packet(create_packet(TYPE_DATA, 0, b"valid"))
        assert verify_checksum(encoded)

    def test_corrupted_payload_detected(self):
        encoded = bytearray(
            encode_packet(create_packet(TYPE_DATA, 0, b"Hello"))
        )
        encoded[-1] ^= 0xFF  # Flip last byte of payload.
        assert not verify_checksum(bytes(encoded))

    def test_corrupted_header_detected(self):
        encoded = bytearray(
            encode_packet(create_packet(TYPE_DATA, 0, b"Hello"))
        )
        # Corrupt the sequence-number bytes (offset 6–9).
        encoded[6] ^= 0x01
        assert not verify_checksum(bytes(encoded))

    def test_corrupted_checksum_raises(self):
        encoded = bytearray(
            encode_packet(create_packet(TYPE_DATA, 0, b"Hello"))
        )
        encoded[-1] ^= 0xFF
        with pytest.raises(PacketError, match="Checksum"):
            decode_packet(bytes(encoded))


# ===================================================================
# Truncated and malformed packets
# ===================================================================

class TestMalformed:
    def test_too_short(self):
        with pytest.raises(PacketError, match="too short"):
            decode_packet(b"\x00" * 10)

    def test_truncated_payload(self):
        encoded = encode_packet(create_packet(TYPE_DATA, 0, b"Hello"))
        with pytest.raises(PacketError, match="mismatch"):
            decode_packet(encoded[:-1])

    def test_extra_trailing_bytes(self):
        encoded = encode_packet(create_packet(TYPE_DATA, 0, b"Hello"))
        with pytest.raises(PacketError, match="mismatch"):
            decode_packet(encoded + b"\x00")

    def test_wrong_magic(self):
        encoded = bytearray(
            encode_packet(create_packet(TYPE_DATA, 0, b"Hello"))
        )
        encoded[0:4] = b"XXXX"
        with pytest.raises(PacketError, match="protocol identifier"):
            decode_packet(bytes(encoded))

    def test_unsupported_version(self):
        encoded = bytearray(
            encode_packet(create_packet(TYPE_DATA, 0, b"Hello"))
        )
        encoded[4] = 99  # Version field.
        with pytest.raises(PacketError, match="version"):
            decode_packet(bytes(encoded))

    def test_non_bytes_datagram(self):
        with pytest.raises(PacketError, match="must be bytes"):
            decode_packet("not bytes")


# ===================================================================
# Unsupported packet types
# ===================================================================

class TestUnsupportedTypes:
    def test_create_invalid_type(self):
        with pytest.raises(PacketError, match="Unsupported packet type"):
            create_packet(99, 0, b"Hello")

    def test_create_type_zero(self):
        with pytest.raises(PacketError, match="Unsupported packet type"):
            create_packet(0, 0, b"")

    def test_decode_invalid_type_in_wire_data(self):
        """Manually craft a datagram with an invalid type byte."""
        encoded = bytearray(
            encode_packet(create_packet(TYPE_DATA, 0, b"Hello"))
        )
        encoded[5] = 99  # Packet-type field.
        with pytest.raises(PacketError):
            decode_packet(bytes(encoded))


# ===================================================================
# Oversized payload
# ===================================================================

class TestOversized:
    def test_one_byte_over_limit(self):
        with pytest.raises(PacketError, match="too large"):
            create_packet(TYPE_DATA, 0, b"x" * (MAX_PAYLOAD_SIZE + 1))

    def test_way_over_limit(self):
        with pytest.raises(PacketError, match="too large"):
            create_packet(TYPE_DATA, 0, b"x" * (MAX_PAYLOAD_SIZE * 2))


# ===================================================================
# Input validation
# ===================================================================

class TestInputValidation:
    def test_non_bytes_payload(self):
        with pytest.raises(PacketError, match="must be bytes"):
            create_packet(TYPE_DATA, 0, "string payload")

    def test_negative_sequence(self):
        with pytest.raises(PacketError, match="sequence number"):
            create_packet(TYPE_DATA, -1, b"")

    def test_sequence_too_large(self):
        with pytest.raises(PacketError, match="sequence number"):
            create_packet(TYPE_DATA, 0x1_0000_0000, b"")

    def test_negative_timestamp(self):
        with pytest.raises(PacketError, match="timestamp"):
            create_packet(TYPE_DATA, 0, b"", timestamp_ns=-1)


# ===================================================================
# END-payload helpers
# ===================================================================

class TestEndPayload:
    def test_round_trip(self):
        for count in (0, 1, 100, 0xFFFFFFFF):
            assert decode_end_payload(encode_end_payload(count)) == count

    def test_wrong_size_payload(self):
        with pytest.raises(PacketError, match="size mismatch"):
            decode_end_payload(b"\x00\x00")

    def test_negative_count(self):
        with pytest.raises(PacketError):
            encode_end_payload(-1)
