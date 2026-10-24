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
    TYPE_ACK,
    TYPE_DATA,
    TYPE_END,
    VERSION,
    Packet,
    PacketError,
    create_ack_packet,
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


# ===================================================================
# ACK packet tests
# ===================================================================

class TestAckPacket:
    """Tests for TYPE_ACK packets, creation, encoding, and validation."""

    def test_create_ack_packet_helper(self):
        ack = create_ack_packet(sequence_number=5)
        assert ack.packet_type == TYPE_ACK
        assert ack.sequence_number == 5
        assert ack.payload == b""
        assert isinstance(ack.timestamp_ns, int)
        assert ack.timestamp_ns > 0

    def test_create_ack_via_create_packet(self):
        ack = create_packet(TYPE_ACK, 10, payload=b"")
        assert ack.packet_type == TYPE_ACK
        assert ack.sequence_number == 10
        assert ack.payload == b""

    def test_ack_explicit_timestamp(self):
        ts = 1234567890123456
        ack = create_ack_packet(7, timestamp_ns=ts)
        assert ack.timestamp_ns == ts

    def test_ack_packet_round_trip(self):
        ack = create_ack_packet(42, timestamp_ns=999999999)
        encoded = encode_packet(ack)

        # Wire size must be exactly 52 bytes (HEADER_SIZE with 0 payload)
        assert len(encoded) == HEADER_SIZE

        decoded = decode_packet(encoded)
        assert decoded.packet_type == TYPE_ACK
        assert decoded.sequence_number == 42
        assert decoded.timestamp_ns == 999999999
        assert decoded.payload == b""

    def test_ack_checksum_validation(self):
        ack = create_ack_packet(15)
        encoded = encode_packet(ack)
        assert verify_checksum(encoded) is True

        # Corrupt sequence number in header
        corrupted = bytearray(encoded)
        corrupted[9] ^= 0xFF
        assert verify_checksum(bytes(corrupted)) is False
        with pytest.raises(PacketError, match="Checksum verification failed"):
            decode_packet(bytes(corrupted))

    def test_ack_with_non_empty_payload_fails_creation(self):
        with pytest.raises(PacketError, match="ACK packet payload must be empty"):
            create_packet(TYPE_ACK, 0, payload=b"unexpected")

    def test_ack_with_payload_on_wire_fails_decode(self):
        # Craft an ACK datagram with payload length > 0
        ack = create_packet(TYPE_DATA, 5, payload=b"foo")
        encoded = bytearray(encode_packet(ack))
        # Change type from TYPE_DATA (1) to TYPE_ACK (3)
        encoded[5] = TYPE_ACK
        with pytest.raises(PacketError, match="ACK datagram payload length must be 0"):
            decode_packet(bytes(encoded))

    def test_ack_sequence_boundaries(self):
        # 0 is valid
        ack0 = create_ack_packet(0)
        assert ack0.sequence_number == 0

        # 2^32 - 1 is valid
        max_seq = 0xFFFFFFFF
        ack_max = create_ack_packet(max_seq)
        assert ack_max.sequence_number == max_seq

        # Negative seq fails
        with pytest.raises(PacketError, match="sequence number"):
            create_ack_packet(-1)

        # Overflow seq fails
        with pytest.raises(PacketError, match="sequence number"):
            create_ack_packet(0x1_0000_0000)

    def test_ack_for_end_packet(self):
        # Acknowledging an END packet which carries total_packets
        total_packets = 250
        ack = create_ack_packet(sequence_number=total_packets)
        encoded = encode_packet(ack)
        decoded = decode_packet(encoded)
        assert decoded.packet_type == TYPE_ACK
        assert decoded.sequence_number == total_packets
        assert decoded.payload == b""
