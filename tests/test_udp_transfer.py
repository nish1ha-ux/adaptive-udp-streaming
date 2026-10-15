
import socket
import subprocess
import sys
import time
from pathlib import Path

from src.packet import MAX_PAYLOAD_SIZE


ROOT = Path(__file__).resolve().parents[1]


def test_local_udp_file_transfer(tmp_path):
    source = tmp_path / "source.bin"
    destination = tmp_path / "received.bin"

    # Generate data large enough to require multiple packets.
    original = bytes(range(256)) * 20
    source.write_bytes(original)

    expected_packets = (
        len(original) + MAX_PAYLOAD_SIZE - 1
    ) // MAX_PAYLOAD_SIZE

    # Find an available UDP port.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    receiver = subprocess.Popen(
        [
            sys.executable,
            "-m", "src.receiver",
            "--host", "127.0.0.1",
            "--port", str(port),
            "--output", str(destination),
            "--expected-packets", str(expected_packets),
            "--timeout", "10",
        ],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        # Give the receiver time to bind its socket.
        time.sleep(0.5)

        sender = subprocess.run(
            [
                sys.executable,
                "-m", "src.sender",
                str(source),
                "--host", "127.0.0.1",
                "--port", str(port),
                "--interval", "0.005",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert sender.returncode == 0, sender.stderr

        stdout, stderr = receiver.communicate(timeout=12)
        assert receiver.returncode == 0, stdout + stderr
        assert destination.exists()
        assert destination.read_bytes() == original

    finally:
        if receiver.poll() is None:
            receiver.kill()
            receiver.wait()

