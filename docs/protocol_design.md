# AUDP protocol design

AUDP (Adaptive UDP) is a small binary protocol that carries a file over UDP with sequence numbers, checksums,
acknowledgements, retransmission and, in one sender mode, adaptive rate control. This document describes what is
implemented in `src/`. Module owners: packet format, sender and receiver foundation (Nishtha Bhushan); ACKs,
retransmission and RTT (Prakhar Manish Srivastava); rate control, emulator and paced sender (Subham Kumar Sahu).

## 1. Datagram format (version 1)

All multi-byte fields are big-endian (network byte order). Every datagram is a 52-byte header followed by the payload.

| Offset | Size | Field | Notes |
|---|---|---|---|
| 0 | 4 | Magic | ASCII `AUDP`. Anything else is rejected. |
| 4 | 1 | Version | `1`. Other versions are rejected. |
| 5 | 1 | Type | `1` DATA, `2` END, `3` ACK |
| 6 | 4 | Sequence number | uint32 |
| 10 | 8 | Timestamp | uint64, monotonic clock in nanoseconds |
| 18 | 2 | Payload length | uint16, 0 to 1400 |
| 20 | 32 | Checksum | SHA-256 |
| 52 | N | Payload | N bytes, as given by the length field |

Struct format: `!4sBBIQH32s`. Maximum datagram: 52 + 1400 = 1452 bytes, which fits a 1500-byte Ethernet MTU after
the IP and UDP headers.

### Checksum

SHA-256 over the packed header fields (magic, version, type, sequence number, timestamp, payload length) followed by
the payload. The checksum is computed before it is written into the header. On receipt it is recomputed and compared
with `hmac.compare_digest` (constant time). A mismatch raises `PacketError` and the datagram is dropped.

### Validation on decode

A datagram is rejected, never partially trusted, when any of these hold: shorter than 52 bytes; wrong magic or
version; unknown type; payload length above 1400; datagram size different from header size plus the length field;
ACK with a non-empty payload; checksum mismatch. All failures raise `PacketError` (a `ValueError`).

## 2. Packet types

| Type | Sequence number means | Payload |
|---|---|---|
| DATA | Zero-based chunk index | 1 to 1400 bytes of file data (0 bytes only if the file is empty) |
| END | Total number of DATA packets | 4-byte big-endian count, repeated so the receiver can check completeness |
| ACK | The sequence number being acknowledged (a chunk index, or the END sequence number) | Empty. The timestamp echoes the acknowledged packet's, so the sender can compute RTT. |

## 3. Stream layout

A file of L bytes becomes `ceil(L / 1400)` DATA packets numbered from 0, then one END packet whose sequence number and
payload both equal that count. An empty file sends zero DATA packets and an END with count 0.

## 4. Receiver (`src/receiver.py`)

1. Bind a UDP socket and wait for datagrams (default timeout 30 s).
2. Decode and validate each datagram. Invalid datagrams are logged and ignored.
3. Store each DATA payload by sequence number. A repeated sequence number is counted as a duplicate and is not stored
   twice, so out-of-order and duplicated delivery cannot corrupt the file.
4. END records the expected packet count. If DATA packets are still missing, the receiver waits up to
   `post_end_timeout` (default 5 s) for stragglers.
5. When every packet from 0 to count-1 is present, the payloads are joined in order and written to the output file, and
   the SHA-256 of the result is logged. If any are missing, it fails with the list of missing sequence numbers instead
   of writing a partial file.
6. After finishing it lingers briefly (default 1 s) to re-acknowledge a retransmitted END if the last ACK was lost.

Acknowledgement modes (`arq`):

| Mode | Behaviour |
|---|---|
| `stop-and-wait` (default) | ACK each in-order DATA packet. A duplicate is re-acknowledged. A packet ahead of the expected one is not acknowledged with its own number, and ACK 0 is never sent before packet 0 arrives. |
| `selective` | ACK every valid DATA packet with its own sequence number. Used by the paced sender, which has several packets in flight. `send_ack=True` selects this mode. |
| `none` | No ACKs (fire and forget). |

## 5. Senders

### Stop-and-wait sender (`src/sender.py`)

One packet is in flight at a time. It sends a packet, then waits for the matching ACK. On timeout it retransmits, up to
`max_retries` (default 5), and raises `MaxRetriesExceededError` when exceeded. The END packet is also acknowledged before
the sender finishes. ACKs for other sequence numbers are ignored. `--arq none` disables acknowledgements.

Timeouts come from `RttEstimator` (`src/reliability.py`):

- First sample: `SRTT = R`, `RTTVAR = R / 2`.
- Later samples: `RTTVAR = 0.75 * RTTVAR + 0.25 * |SRTT - R|`, `SRTT = 0.875 * SRTT + 0.125 * R`.
- `RTO = SRTT + 4 * RTTVAR`, clamped to `[min_rto, max_rto]`.
- Karn's algorithm: a packet that was retransmitted gives no RTT sample.
- Each timeout doubles the RTO (exponential back-off), up to `max_rto`.

### Paced sender (`src/paced_sender.py`)

Sends DATA at a controlled rate instead of waiting for each ACK. A listener thread receives ACKs, measures RTT and
jitter (RFC 3550 style), and unacknowledged packets are retransmitted after `ack_timeout` up to
`max_retransmissions`. The rate comes from `AdaptiveRateController` (`src/rate_control.py`):

- `fixed` mode keeps the starting rate.
- `adaptive` mode runs AIMD: if smoothed RTT, jitter or loss exceed their thresholds the rate is multiplied by
  `decrease_factor`; if all are comfortably below, it grows by `increase_step`. Rate stays between `min_rate` and
  `max_rate`, and changes are spaced by `min_adjustment_interval_s`.

## 6. Integrity

The sender logs the SHA-256 of the source file; the receiver logs the SHA-256 of the reconstructed file. Experiments
and the dashboard compare the two bytes-for-bytes and report whether they match.

## 7. Known limits

- Stop-and-wait uses one packet per round trip, so throughput is bounded by the RTT.
- The checksum is SHA-256, not a keyed MAC: it detects corruption, not tampering.
- Sequence numbers are 32-bit and do not wrap; a stream is limited to 2^32 packets.
- The paced sender's rate controller uses absolute RTT thresholds, so propagation delay is read as congestion unless
  the thresholds are tuned (see `docs/emulator_review.md` and the experiment results).
