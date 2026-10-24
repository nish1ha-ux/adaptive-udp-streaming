"""Unit and integration tests for NetworkEmulator."""

import socket
import time
import pytest

from src.emulator import EmulatorConfig, NetworkEmulator, UDPRelayServer
from src.packet import (
    TYPE_DATA,
    TYPE_END,
    create_packet,
    decode_packet,
    encode_packet,
)


def test_emulator_config_validation():
    """Verify that invalid configuration values are properly rejected."""
    # Valid configurations
    cfg = EmulatorConfig(loss_probability=0.0, base_delay_ms=0.0, jitter_ms=0.0)
    cfg.validate()

    cfg = EmulatorConfig(loss_probability=1.0, base_delay_ms=50.0, jitter_ms=10.0, direction="forward")
    cfg.validate()

    # Invalid loss probability
    with pytest.raises(ValueError):
        EmulatorConfig(loss_probability=-0.1)

    with pytest.raises(ValueError):
        EmulatorConfig(loss_probability=1.05)

    # Invalid delay
    with pytest.raises(ValueError):
        EmulatorConfig(base_delay_ms=-1.0)

    # Invalid jitter
    with pytest.raises(ValueError):
        EmulatorConfig(jitter_ms=-5.0)

    # Invalid direction
    with pytest.raises(ValueError):
        EmulatorConfig(direction="invalid_dir")


def test_zero_loss_and_zero_delay():
    """Verify zero impairment forwards all packets unchanged with 0ms delay."""
    cfg = EmulatorConfig(loss_probability=0.0, base_delay_ms=0.0, jitter_ms=0.0)
    emulator = NetworkEmulator(cfg)

    packet = create_packet(TYPE_DATA, sequence_number=42, payload=b"ZeroImpairment")
    encoded = encode_packet(packet)

    for _ in range(50):
        dropped, delay_ms, seq, ptype = emulator.process_packet(encoded)
        assert not dropped
        assert delay_ms == 0.0
        assert seq == 42
        assert ptype == TYPE_DATA

    assert emulator.total_processed == 50
    assert emulator.total_forwarded == 50
    assert emulator.total_dropped == 0


def test_packet_loss_reproducibility_with_seed():
    """Verify that a fixed random seed produces identical drop sequences."""
    cfg1 = EmulatorConfig(loss_probability=0.3, random_seed=12345)
    emu1 = NetworkEmulator(cfg1)

    cfg2 = EmulatorConfig(loss_probability=0.3, random_seed=12345)
    emu2 = NetworkEmulator(cfg2)

    packet = encode_packet(create_packet(TYPE_DATA, 1, b"Payload"))

    results1 = [emu1.process_packet(packet)[0] for _ in range(100)]
    results2 = [emu2.process_packet(packet)[0] for _ in range(100)]

    assert results1 == results2
    # Verify some packets were dropped and some forwarded
    assert any(results1)
    assert not all(results1)
    assert emu1.total_dropped == emu2.total_dropped


def test_100_percent_loss():
    """Verify 100% loss drops every packet."""
    cfg = EmulatorConfig(loss_probability=1.0)
    emulator = NetworkEmulator(cfg)
    packet = encode_packet(create_packet(TYPE_DATA, 1, b"WillBeDropped"))

    for _ in range(20):
        dropped, _, _, _ = emulator.process_packet(packet)
        assert dropped

    assert emulator.total_dropped == 20
    assert emulator.total_forwarded == 0


def test_fixed_delay_without_jitter():
    """Verify fixed delay applies the exact base delay."""
    cfg = EmulatorConfig(base_delay_ms=25.0, jitter_ms=0.0)
    emulator = NetworkEmulator(cfg)
    packet = encode_packet(create_packet(TYPE_DATA, 1, b"Delayed"))

    for _ in range(10):
        dropped, delay_ms, _, _ = emulator.process_packet(packet)
        assert not dropped
        assert delay_ms == 25.0


def test_jitter_distribution_and_non_negative_delay():
    """Verify jitter generates delay variations and never produces negative delay."""
    # base_delay 10ms with jitter 15ms -> range [-5, 25], but clamped to min 0.0
    cfg = EmulatorConfig(base_delay_ms=10.0, jitter_ms=15.0, random_seed=999)
    emulator = NetworkEmulator(cfg)
    packet = encode_packet(create_packet(TYPE_DATA, 1, b"Jittery"))

    delays = []
    for _ in range(100):
        dropped, delay_ms, _, _ = emulator.process_packet(packet)
        assert not dropped
        assert delay_ms >= 0.0
        delays.append(delay_ms)

    assert min(delays) >= 0.0
    assert max(delays) <= 25.0001
    # Check that there is variation
    assert len(set(delays)) > 1


def test_scheduled_packet_forwarding_and_payload_integrity():
    """Verify scheduled delayed forwarding delivers intact datagrams."""
    cfg = EmulatorConfig(loss_probability=0.0, base_delay_ms=20.0, jitter_ms=0.0)
    emulator = NetworkEmulator(cfg)
    emulator.start_worker()

    delivered = []

    def on_forward(data, addr):
        delivered.append((data, addr, time.monotonic()))

    packet = create_packet(TYPE_DATA, sequence_number=7, payload=b"UncorruptedData")
    encoded = encode_packet(packet)

    start_time = time.monotonic()
    scheduled = emulator.schedule_packet(encoded, ("127.0.0.1", 9999), on_forward)
    assert scheduled

    # Wait for worker to deliver
    time.sleep(0.08)
    emulator.stop_worker(drain=True)

    assert len(delivered) == 1
    received_bytes, addr, delivery_time = delivered[0]
    assert addr == ("127.0.0.1", 9999)

    # Check payload integrity
    decoded = decode_packet(received_bytes)
    assert decoded.sequence_number == 7
    assert decoded.payload == b"UncorruptedData"

    # Verify that delay was approximately respected (>= 18ms)
    elapsed = delivery_time - start_time
    assert elapsed >= 0.018


def test_safe_shutdown_with_drain():
    """Verify emulator gracefully drains pending packets on shutdown."""
    cfg = EmulatorConfig(base_delay_ms=30.0, jitter_ms=0.0)
    emulator = NetworkEmulator(cfg)
    emulator.start_worker()

    delivered = []
    encoded = encode_packet(create_packet(TYPE_DATA, 1, b"DrainTest"))
    emulator.schedule_packet(encoded, ("127.0.0.1", 1234), lambda d, a: delivered.append(d))

    # Drain during stop
    emulator.stop_worker(drain=True, timeout=1.0)
    assert len(delivered) == 1


def test_direction_filtering():
    """Verify directional impairment filtering for forward vs reverse traffic."""
    # Impair forward only
    cfg = EmulatorConfig(loss_probability=1.0, direction="forward")
    emulator = NetworkEmulator(cfg)

    packet = encode_packet(create_packet(TYPE_DATA, 1, b"Test"))

    # Forward direction is dropped
    dropped_fwd, _, _, _ = emulator.process_packet(packet, direction="forward")
    assert dropped_fwd

    # Reverse direction is NOT dropped
    dropped_rev, _, _, _ = emulator.process_packet(packet, direction="reverse")
    assert not dropped_rev


def test_udp_relay_server_integration():
    """Test UDP relay server actively passing packets between sockets."""
    # Find two available ports
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s1:
        s1.bind(("127.0.0.1", 0))
        relay_port = s1.getsockname()[1]

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s2:
        s2.bind(("127.0.0.1", 0))
        dest_port = s2.getsockname()[1]

    cfg = EmulatorConfig(loss_probability=0.0, base_delay_ms=10.0, jitter_ms=0.0)
    relay = UDPRelayServer(
        listen_host="127.0.0.1",
        listen_port=relay_port,
        forward_host="127.0.0.1",
        forward_port=dest_port,
        config=cfg,
    )
    relay.start()

    receiver_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    receiver_sock.bind(("127.0.0.1", dest_port))
    receiver_sock.settimeout(2.0)

    sender_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    try:
        # Sender sends to relay
        packet = encode_packet(create_packet(TYPE_DATA, 100, b"ThroughRelay"))
        sender_sock.sendto(packet, ("127.0.0.1", relay_port))

        # Receiver should get packet via relay
        data, addr = receiver_sock.recvfrom(2048)
        decoded = decode_packet(data)
        assert decoded.sequence_number == 100
        assert decoded.payload == b"ThroughRelay"
    finally:
        relay.stop(drain=False)
        receiver_sock.close()
        sender_sock.close()
