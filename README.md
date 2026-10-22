
<div align="center">

# 🚀 Adaptive UDP Streaming
An adaptive UDP-based streaming protocol designed for reliable and efficient real-time data delivery under varying network conditions. The project implements custom packet handling, acknowledgements, retransmission, RTT/jitter measurement, adaptive transmission-rate control, and a network emulator for performance evaluation.
### Adaptive Transport Protocol for Real-Time Streaming over UDP

<p>
  <img src="https://img.shields.io/badge/Language-Python-blue?style=for-the-badge&logo=python">
  <img src="https://img.shields.io/badge/Protocol-UDP-orange?style=for-the-badge">
  <img src="https://img.shields.io/badge/Project-Computer%20Networks-green?style=for-the-badge">
  <img src="https://img.shields.io/badge/Status-In%20Development-yellow?style=for-the-badge">
</p>

</div>

---

## 📌 Overview

**Adaptive UDP Streaming** is a custom transport mechanism designed for
reliable and efficient real-time data delivery over UDP.

The protocol monitors network conditions such as **packet loss, RTT and
jitter** and dynamically adjusts the transmission rate.

A custom **network emulator** is used to introduce controlled loss,
delay and jitter for evaluating the protocol under different network
conditions.

> **Core idea:** Adapt the transmission rate according to network
> conditions instead of using a fixed transmission rate.

---

## 🎯 Objectives

- Implement UDP communication using sockets.
- Design a custom packet format with sequence numbers and checksums.
- Implement acknowledgements and retransmission.
- Measure RTT and network jitter.
- Develop adaptive transmission-rate control.
- Build a configurable network emulator.
- Reconstruct and verify the received stream.
- Compare adaptive and fixed-rate UDP transmission.

---

## 🏗️ System Architecture

```mermaid
flowchart LR
    A[Streaming Data] --> B[UDP Sender]
    B --> C[Adaptive Rate Controller]
    C --> D[Network Emulator]
    D --> E[UDP Receiver]
    E --> F[Stream Reconstruction]
    E --> G[ACK / Feedback]
    G --> C

    D -. Packet Loss .-> D
    D -. Delay .-> D
    D -. Jitter .-> D
```

### Data Flow

```text
Streaming Data
      ↓
 UDP Sender
      ↓
Packet Formation
      ↓
Adaptive Rate Control
      ↓
Network Emulator
      ↓
 UDP Receiver
      ↓
Stream Reconstruction
      ↓
Received Data
```

---

## ⚙️ Core Components

<details>
<summary><b>1. UDP Sender</b></summary>

Responsible for reading streaming data, creating packets, maintaining
sequence numbers and controlling the transmission rate.

</details>

<details>
<summary><b>2. UDP Receiver</b></summary>

Receives packets, validates their integrity, detects missing packets,
sends acknowledgements and reconstructs the stream.

</details>

<details>
<summary><b>3. Packet Format</b></summary>

Each packet contains information required for transmission and
reliability, including:

- Sequence number
- Timestamp
- Payload
- Checksum

</details>

<details>
<summary><b>4. Reliability Mechanism</b></summary>

Acknowledgements and retransmissions are used to detect and recover
from packet loss.

</details>

<details>
<summary><b>5. Network Emulator</b></summary>

The emulator introduces controlled network impairments:

- Packet loss
- Delay
- Jitter

</details>

<details>
<summary><b>6. Adaptive Rate Control</b></summary>

The sender uses measured network conditions such as packet loss,
RTT and jitter to adjust its transmission rate.

</details>

---

## 🧪 Experiments

The protocol will be evaluated under different network conditions.

### Variables

| Variable | Description |
|---|---|
| Packet Loss | Percentage of packets intentionally dropped |
| Delay | Artificial network delay |
| Jitter | Variation in packet delay |
| Transmission Rate | Sender's data transmission rate |

### Performance Metrics

| Metric | Purpose |
|---|---|
| **Goodput** | Effective successfully delivered data |
| **Packet Loss** | Percentage of packets lost |
| **Latency** | End-to-end delivery delay |
| **Jitter** | Variation in packet delay |
| **Retransmissions** | Number of packets retransmitted |

### Experimental Claim

> **Adaptive transmission will provide better and more stable streaming
> performance than fixed-rate UDP under varying network conditions.**

Results will be presented using graphs and comparative analysis.

---

## 🛠️ Technology Stack

| Technology | Purpose |
|---|---|
| **Python** | Core implementation |
| `socket` | UDP communication |
| `threading` | Concurrent operations |
| `time` | Timing and RTT measurement |
| `hashlib` | Data integrity verification |
| `matplotlib` | Performance visualization |

The core networking mechanism is implemented using socket-level
communication rather than an existing library that provides the required
protocol functionality.

---

## 📁 Project Structure

```text
adaptive-udp-streaming/
│
├── src/
│   ├── sender.py
│   ├── receiver.py
│   ├── packet.py
│   ├── reliability.py
│   ├── rate_control.py
│   └── emulator.py
│
├── tests/
│
├── experiments/
│
├── results/
│
├── docs/
│
├── requirements.txt
├── README.md
└── AI-USE.md
```

> The project structure will evolve as implementation progresses.

---

## 👥 Team

| Member | Responsibility |
|---|---|
| **Nishtha Bhushan** | UDP communication & packet format |
| **Prakhar Manish Srivastava** | ACK, retransmission & RTT |
| **Subham Kumar Sahu** | Adaptive rate control & network emulator |
| **Sudhanshu Aryan** | Testing, experiments & analysis |

---

## 📅 Development Plan

| Week | Target |
|---|---|
| 1 | Project design |
| 2 | Packet handling & streaming |
| 3 | ACK & retransmission |
| 4 | Network emulator |
| 5 | Adaptive mechanism |
| 6 | Integration & testing |
| 7 | Experiments & graphs |
| 8 | Finalization & viva |

---

## 🚧 Project Status

**Current Status:** 🟡 In Development

- [x] UDP communication
- [x] Custom packet format
- [x] Reliability mechanism
- [x] RTT measurement
- [x] Network emulator
- [x] Adaptive rate control
- [x] Testing
- [x] Performance experiments
- [x] Final documentation

---

<div align="center">

### CS-30003 · Coding Assignment 1

**Adaptive Transport Protocol for Real-Time Streaming over UDP**

</div>
