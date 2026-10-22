# Network Emulator & Adaptive Transmission-Rate Control

**Component Owner**: Subham Kumar Sahu  
**Project**: Adaptive UDP Streaming  
**Target Modules**: `src/emulator.py`, `src/rate_control.py`, `src/sender.py`, `src/receiver.py`

---

## 1. Network Emulator Architecture (`src/emulator.py`)

### 1.1 Overview
The Network Emulator provides reproducible simulation of real-world IP network impairments:
1. **Packet Loss**: Evaluated using a seeded pseudo-random number generator (`random.Random(seed)`).
2. **Fixed Base Delay**: Configurable non-negative forwarding latency in milliseconds.
3. **Random Jitter**: Delay variation drawn uniformly from `[-jitter_ms, +jitter_ms]`, with the effective delay strictly clamped to non-negative values (`max(0.0, base_delay + jitter)`).
4. **Directional Impairments**: Configurable filtering for `forward` (sender -> receiver), `reverse` (receiver -> sender), or `both`.

### 1.2 Dual Operating Modes
- **In-Process Engine (`NetworkEmulator`)**:
  - Deterministic method `process_packet(raw_data, direction)` returning `(dropped, delay_ms, seq_num, packet_type)`.
  - Non-blocking worker queue (`PriorityQueue`) releasing delayed datagrams via monotonic scheduling callbacks without busy-waiting.
- **Standalone UDP Relay (`UDPRelayServer`)**:
  - Acts as a network proxy between sender and receiver (`Sender -> Relay:5004 -> Receiver:5005`).
  - Transparently relays DATA forward and ACK backward while applying configured impairment profiles.

### 1.3 Configuration Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `loss_probability` | `float` | `0.0` | Probability of packet drop in range `[0.0, 1.0]`. |
| `base_delay_ms` | `float` | `0.0` | Base one-way latency in milliseconds (`>= 0.0`). |
| `jitter_ms` | `float` | `0.0` | Jitter variation amplitude in milliseconds (`>= 0.0`). |
| `random_seed` | `int` / `None` | `None` | Random seed for exact experiment reproducibility. |
| `direction` | `str` | `"both"` | Impairment direction: `"both"`, `"forward"`, `"reverse"`. |

---

## 2. Adaptive Transmission-Rate Controller (`src/rate_control.py`)

### 2.1 Overview & Metrics
The controller regulates the sender's transmission rate using an **AIMD (Additive Increase, Multiplicative Decrease)** rule-based algorithm.

#### Measured Metrics & Units:
- **Transmission Rate ($R$)**: Measured in **packets per second (pkt/s)**. Inter-packet interval is computed as $T_{\text{interval}} = \frac{1}{R}$ seconds.
- **Round-Trip Time ($\text{RTT}$)**: Measured in **milliseconds (ms)** from packet header timestamps echoed in receiver ACKs. Smoothed using Exponentially Weighted Moving Average (EWMA):
  $$\text{SRTT}_{t} = (1 - \alpha) \cdot \text{SRTT}_{t-1} + \alpha \cdot \text{RTT}_{\text{sample}} \quad (\alpha = 0.125)$$
- **Jitter ($J$)**: RFC 3550 standard inter-arrival delay variation in **milliseconds (ms)**:
  $$J_{t} = J_{t-1} + \frac{|\Delta \text{RTT}| - J_{t-1}}{16}$$
- **Packet Loss Ratio ($L$)**: Fraction in range `[0.0, 1.0]` of unacknowledged packets over total transmitted packets in the observation window.

### 2.2 Control Rules & Conditions

1. **Condition A — Network Congestion / Deterioration**:
   - Triggered when:
     - $L > \text{loss\_threshold}$ (e.g. $> 3\%$), OR
     - $\text{SRTT} > \text{rtt\_threshold\_ms}$ (e.g. $> 80\text{ ms}$), OR
     - $\text{Jitter} > \text{jitter\_threshold\_ms}$ (e.g. $> 20\text{ ms}$).
   - Action (**Multiplicative Decrease**):
     $$R_{\text{new}} = \max(R_{\text{min}}, R_{\text{current}} \times \text{decrease\_factor}) \quad (\text{factor} = 0.7)$$
2. **Condition B — Stable Network Conditions**:
   - Triggered when metrics are comfortably below thresholds:
     - $L \le \frac{\text{loss\_threshold}}{2}$ AND $\text{SRTT} \le 0.8 \times \text{rtt\_threshold}$ AND $\text{Jitter} \le 0.8 \times \text{jitter\_threshold}$.
   - Action (**Additive Increase**):
     $$R_{\text{new}} = \min(R_{\text{max}}, R_{\text{current}} + \text{increase\_step}) \quad (\text{step} = 10\text{ pkt/s})$$
3. **Condition C — Deadband & Uncertain Measurements**:
   - When measurements lie between stable and congestion limits, or when metrics are missing/stale, rate is preserved (`stable_hold` or fallback).
4. **Condition D — Strict Rate Bounds**:
   - Rate is strictly clamped between $R_{\text{min}}$ and $R_{\text{max}}$ to prevent starvation or socket buffer overflow.

### 2.3 Anti-Oscillation Safeguards
- **Hysteresis Interval (`min_adjustment_interval_s`)**: Prevents rapid back-to-back rate updates by enforcing a minimum settling time (default `0.05s`).
- **EWMA Smoothing**: Filters out transient single-packet spikes.

---

## 3. Fixed-Rate vs. Adaptive Operating Modes

- **Fixed-Rate Mode (`--mode fixed`)**:
  - Uses a constant pacing interval (`--interval <seconds>`).
  - Rate controller remains active in passthrough mode, logging observed metrics without modifying the transmission rate.
- **Adaptive-Rate Mode (`--mode adaptive`)**:
  - Dynamically modulates the inter-packet interval using real-time ACK feedback.

---

## 4. Execution & CLI Commands

### Running Unit and Integration Tests
```powershell
pytest -v
```

### Starting the Network Emulator Relay
```powershell
python -m src.emulator --listen-port 5004 --forward-port 5005 --loss 0.05 --delay 50 --jitter 15 --seed 42
```

### Starting the Receiver
```powershell
python -m src.receiver --port 5005 --output received_output.bin
```

### Starting the Sender in Adaptive Mode
```powershell
python -m src.sender test_data/sample.txt --port 5004 --mode adaptive --interval 0.005
```

### Starting the Sender in Fixed-Rate Mode
```powershell
python -m src.sender test_data/sample.txt --port 5004 --mode fixed --interval 0.005
```

### Running the End-to-End Experiment Matrix (E1 to E4)
```powershell
python -m experiments.run_experiments --size-kb 30
```
Outputs are written to:
- `results/experiment_results.json`
- `results/experiment_results.csv`
- `results/performance_comparison.png`
