"""Tests for the bandwidth-limited bottleneck (requires the emulator branch's src/emulator.py)."""

import pytest

pytest.importorskip("src.emulator")

from experiments.bottleneck_link import BottleneckEmulator  # noqa: E402
from src.emulator import EmulatorConfig, NetworkEmulator  # noqa: E402
from src.packet import TYPE_ACK, TYPE_DATA, create_packet, encode_packet  # noqa: E402

KBPS = 1000
PACKET = encode_packet(create_packet(TYPE_DATA, 0, b"x" * 1400))
SERVICE_S = len(PACKET) * 8 / (KBPS * 1000)


def _emu(**kw):
    return BottleneckEmulator(EmulatorConfig(random_seed=1), bandwidth_kbps=kw.pop("kbps", KBPS), **kw)


def test_unlimited_matches_base_emulator():
    cfg = dict(loss_probability=0.3, base_delay_ms=10.0, jitter_ms=4.0, random_seed=7)
    base, wrapped = NetworkEmulator(EmulatorConfig(**cfg)), BottleneckEmulator(EmulatorConfig(**cfg))
    for _ in range(50):
        assert base.process_packet(PACKET)[:2] == wrapped.process_packet(PACKET)[:2]


def test_idle_link_delay_is_service_time():
    dropped, delay_ms, _, _ = _emu().process_packet(PACKET, now=100.0)
    assert not dropped and delay_ms == pytest.approx(SERVICE_S * 1000)


def test_burst_queues_one_service_time_each():
    emu = _emu()
    delays = [emu.process_packet(PACKET, now=100.0)[1] for _ in range(5)]
    assert delays == pytest.approx([SERVICE_S * 1000 * (i + 1) for i in range(5)])


def test_overflow_is_tail_dropped_and_counted():
    emu = _emu(queue_packets=3)
    results = [emu.process_packet(PACKET, now=100.0)[0] for _ in range(6)]
    assert results == [False, False, False, False, True, True]
    assert emu.congestion_drops == 2 and emu.total_dropped == 2


def test_sending_below_capacity_never_queues():
    emu = _emu()
    for i in range(20):
        dropped, delay_ms, _, _ = emu.process_packet(PACKET, now=100.0 + i * SERVICE_S * 2)
        assert not dropped and delay_ms == pytest.approx(SERVICE_S * 1000)


def test_reverse_acks_are_not_limited():
    ack = encode_packet(create_packet(TYPE_ACK, 0, b""))
    emu = _emu()
    for _ in range(20):
        assert emu.process_packet(ack, direction="reverse", now=100.0)[:2] == (False, 0.0)


def test_validation():
    with pytest.raises(ValueError):
        BottleneckEmulator(bandwidth_kbps=-1)
    with pytest.raises(ValueError):
        BottleneckEmulator(queue_packets=0)
