"""End-to-End Integration tests connecting Sender, Emulator, and Receiver."""

import socket
import threading
import time
from pathlib import Path
import pytest

from src.emulator import EmulatorConfig, UDPRelayServer
from src.receiver import receive_file
from src.paced_sender import send_file
from src.rate_control import AdaptiveRateController, RateControllerConfig, RateControlMode


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_transfer_through_emulator_ideal_conditions(tmp_path):
    """Test full transfer through emulator with 0% loss and 0ms delay."""
    source_file = tmp_path / "source_ideal.bin"
    received_file = tmp_path / "received_ideal.bin"

    # 10 KB file (~8 packets)
    content = bytes(range(256)) * 40
    source_file.write_bytes(content)

    relay_port = find_free_port()
    recv_port = find_free_port()

    relay_cfg = EmulatorConfig(loss_probability=0.0, base_delay_ms=0.0, jitter_ms=0.0)
    relay = UDPRelayServer(
        listen_port=relay_port,
        forward_port=recv_port,
        config=relay_cfg,
    )
    relay.start()

    recv_thread = threading.Thread(
        target=receive_file,
        args=(str(received_file),),
        kwargs={"port": recv_port, "timeout": 5.0, "send_ack": True},
        daemon=True,
    )
    recv_thread.start()
    time.sleep(0.1)

    try:
        stats = send_file(
            str(source_file),
            port=relay_port,
            packet_interval=0.005,
            mode="adaptive",
            ack_timeout=0.1,
        )
        recv_thread.join(timeout=5.0)

        assert not recv_thread.is_alive()
        assert received_file.exists()
        assert received_file.read_bytes() == content
        assert stats.data_packets_sent > 0
        assert stats.acks_received > 0
    finally:
        relay.stop(drain=False)


def test_transfer_with_delay_and_jitter_causes_rate_adaptation(tmp_path):
    """Test transfer through emulator with delay and jitter causes adaptive rate adjustment."""
    source_file = tmp_path / "source_impaired.bin"
    received_file = tmp_path / "received_impaired.bin"

    # 15 KB file
    content = bytes(range(256)) * 60
    source_file.write_bytes(content)

    relay_port = find_free_port()
    recv_port = find_free_port()

    # 40ms delay with 15ms jitter
    relay_cfg = EmulatorConfig(
        loss_probability=0.0,
        base_delay_ms=40.0,
        jitter_ms=15.0,
        random_seed=42,
    )
    relay = UDPRelayServer(
        listen_port=relay_port,
        forward_port=recv_port,
        config=relay_cfg,
    )
    relay.start()

    recv_thread = threading.Thread(
        target=receive_file,
        args=(str(received_file),),
        kwargs={"port": recv_port, "timeout": 8.0, "send_ack": True},
        daemon=True,
    )
    recv_thread.start()
    time.sleep(0.1)

    ctrl_cfg = RateControllerConfig(
        mode=RateControlMode.ADAPTIVE,
        initial_rate=150.0,
        rtt_threshold_ms=50.0,
        jitter_threshold_ms=10.0,
        min_adjustment_interval_s=0.01,
    )
    controller = AdaptiveRateController(ctrl_cfg)

    try:
        stats = send_file(
            str(source_file),
            port=relay_port,
            packet_interval=0.005,
            mode="adaptive",
            controller=controller,
            ack_timeout=0.2,
        )
        recv_thread.join(timeout=8.0)

        assert not recv_thread.is_alive()
        assert received_file.exists()
        assert received_file.read_bytes() == content

        # Verify that RTT was measured and rate controller recorded adjustments
        assert stats.average_rtt_ms >= 30.0
        assert len(stats.rate_history) > 0
    finally:
        relay.stop(drain=False)


def test_fixed_rate_vs_adaptive_mode_flag(tmp_path):
    """Test that fixed-rate mode strictly maintains its rate while adaptive adapts."""
    source_file = tmp_path / "source_modes.bin"
    received_file_fixed = tmp_path / "received_fixed.bin"
    received_file_adapt = tmp_path / "received_adapt.bin"

    content = bytes(range(256)) * 20
    source_file.write_bytes(content)

    # 1. Fixed rate test
    recv_port1 = find_free_port()
    recv_thread1 = threading.Thread(
        target=receive_file,
        args=(str(received_file_fixed),),
        kwargs={"port": recv_port1, "timeout": 5.0, "send_ack": True},
        daemon=True,
    )
    recv_thread1.start()
    time.sleep(0.1)

    stats_fixed = send_file(
        str(source_file),
        port=recv_port1,
        packet_interval=0.01,
        mode="fixed",
    )
    recv_thread1.join(timeout=5.0)

    # In fixed mode, final rate must exactly equal initial rate (1 / 0.01 = 100 pkt/s)
    assert stats_fixed.final_rate_pps == pytest.approx(100.0, rel=1e-3)
    assert all(r["reason"] == "fixed_rate_mode" for r in stats_fixed.rate_history)
