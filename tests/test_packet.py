
import pytest

from src.packet import (
    TYPE_DATA,
    TYPE_END,
    PacketError,
    create_packet,
    decode_packet,
    encode_packet,
    verify_checksum,
)


def test_data_packet_round_trip():
    original = create_packet(TYPE_DATA, 0, b"Hello UDP")

    encoded = encode_packet(original)
    decoded = decode_packet(encoded)

    assert decoded.packet_type == TYPE_DATA
    assert decoded.sequence_number == 0
    assert decoded.payload == b"Hello UDP"


def test_end_packet_round_trip():
    original = create_packet(TYPE_END, 1, b"")

    decoded = decode_packet(encode_packet(original))

    assert decoded.packet_type == TYPE_END
    assert decoded.payload == b""


def test_corrupted_packet_is_rejected():
    encoded = bytearray(
        encode_packet(create_packet(TYPE_DATA, 0, b"Hello"))
    )

    encoded[-1] ^= 1

    assert not verify_checksum(bytes(encoded))


def test_truncated_packet_is_rejected():
    encoded = encode_packet(
        create_packet(TYPE_DATA, 0, b"Hello")
    )

    with pytest.raises(PacketError):
        decode_packet(encoded[:-1])


def test_payload_too_large_is_rejected():
    with pytest.raises(PacketError):
        create_packet(TYPE_DATA, 0, b"x" * 1401)


def test_invalid_packet_type_is_rejected():
    with pytest.raises(PacketError):
        create_packet(99, 0, b"Hello")

