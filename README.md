# Reflex Rush — Clinical Brain Speed Assessment

> A real-time, hardware-integrated reaction time game built in 24 hours at **YHack 2026**.  
> Measures choice reaction time against clinical age/sex norms, with blink-rate tracking and a live leaderboard.

---

## What It Does

Reflex Rush is a 4-color choice reaction time (CRT) test running on a **Raspberry Pi** with physical LEDs, controlled remotely from a laptop over the **Viam robotics SDK**. A color lights up on the dashboard → the player presses the corresponding keyboard key → the system records their reaction time in milliseconds and scores it against published clinical norms for their age and sex.

Clinical applications include early screening for cognitive slowing associated with Parkinson's disease, MCI, and age-related neurodegeneration. The game also tracks **blink rate** via MediaPipe face mesh — an established early marker in Parkinson's research.

---

## Demo

### Demo Video

![Demo Video](assets/demo_gameplay.mp4)

```
Session 1 → 5 rounds → Session 2 → 5 rounds → Session 3 → 5 rounds → Scorecard
```

Each round: random color flashes → player hits matching key → RT recorded → result displayed with live clinical feedback.
---

## Hardware

### Components

| Component | Details |
|---|---|
| Raspberry Pi | Any model with GPIO (tested on Pi 4) |
| LEDs | Red, Green, Blue, Yellow — GPIO pins 12, 7, 38, 36 |
| Camera | Optional — USB or Pi camera for blink detection + live feed |
| Laptop | Runs the Flask server + dashboard; communicates with Pi via Viam |

### Hardware Setup

![Raspberry Pi](assets/pi.jfif)

![Control Board](assets/board.jfif)

### Camera

![Pi Camera](assets/camera.jfif)

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                    LAPTOP                           │
│                                                     │
│   flexrush_server.py (Flask API)                    │
│   ├── /api/state      → game state (polled 10x/sec) │
│   ├── /api/register   → player name + start game    │
│   ├── /api/input      → keyboard key presses        │
│   ├── /api/blink      → blink rate from dashboard   │
│   ├── /api/leaderboard→ persistent JSON scores      │
│   └── /api/camera_stream → MJPEG from Pi camera    │
│                                                     │
│   dashboard.html (Dashboard — open in browser)      │
│   ├── Live camera feed                              │
│   ├── Color orbs + keyboard control (RGBY keys)    │
│   ├── Clinical speed range chart (age-adjusted)     │
│   ├── Blink detection via MediaPipe FaceMesh        │
│   └── Scorecard modal + leaderboard                 │
└────────────────────┬────────────────────────────────┘
                     │ Viam SDK (cloud relay)
┌────────────────────▼────────────────────────────────┐
│                 RASPBERRY PI                        │
│   GPIO: LEDs                                        │
│   Camera (streamed back to laptop)                  │
└─────────────────────────────────────────────────────┘
```

---

## Scoring Algorithm

### Expected RT Formula

```
expected_ms = 240 + (age − 20) × 0.5 + sex_offset
sex_offset  = −10ms (male) / +10ms (female)
```

Validated ranges across ages 30–80:

| Sex | Elite lower bound | Healthy range (age 30) | Full normal band |
|---|---|---|---|
| Male | < 185ms | 185–310ms | 185–465ms |
| Female | < 205ms | 205–330ms | 205–480ms |

### Classification Bands

```
Elite:        diff < −50ms     (≥50ms faster than expected)
Healthy:  −50 ≤ diff ≤ +75ms  (normal 4-choice RT for age/sex)
Average:  +75 < diff ≤ +175ms (mild slowing — trend to watch)
Concern:       diff > +175ms   (clinically meaningful slowing)
```

> **Why these thresholds?** The baseline (240ms) and bands are calibrated for a **4-choice** task per Hick's Law and Kosinski (2008). Simple RT norms (~215ms) used by earlier versions caused healthy players scoring 280–350ms to be mis-classified as "Below Average." The current thresholds exactly reproduce the validated clinical ranges above.

### Leaderboard Score

```
score = median_rt / accuracy
```

Accuracy-weighted so a fast-but-sloppy player can't beat a consistent one. Lower = better.

### Consistency (CV)

Coefficient of variation (`SD / mean`) of correct-hit RTs. Elevated CV is a known early marker in MCI and Parkinson's research (Hultsch et al. 2002).

### Error Handling

| Error type | Treatment | Rationale |
|---|---|---|
| Missed (no response) | RT excluded from median | Accuracy event, not speed event (Jensen 2006) |
| Wrong key | RT + 300ms penalty included | Commission error — they reacted, penalize decision |
| False start | Trial restarted, not recorded | Standard clinical CRT protocol (Luce 1986) |

---

## Setup

### Prerequisites

```bash
pip install flask flask-cors viam-sdk
```

### Viam Configuration

1. Create a robot at [app.viam.com](https://app.viam.com)
2. Add a `board` component named `board-1`
3. Optionally add a `camera` component named `camera-1`
4. Copy your robot address, API key, and key ID into `flexrush3.py`:

```python
VIAM_ADDRESS = "your-robot.viam.cloud"
VIAM_API_KEY = "your-api-key"
VIAM_KEY_ID  = "your-key-id"
```

### GPIO Pin Mapping

```python
LED_PINS = {"RED": "12", "GREEN": "7", "BLUE": "38", "YELLOW": "36"}
```

Adjust these to match your physical LED wiring on the Raspberry Pi.

### Run

```bash
python flexrush3.py
# Enter default player age and sex when prompted
# Then open d2.html in a browser on the same machine
```

---

## Controls

| Key | Action |
|---|---|
| R | RED |
| G | GREEN |
| B | BLUE |
| Y | YELLOW |
| Space or Click | Start session |

---

## File Structure

```
reflex-rush/
├── flexrush3.py      # Flask API + game logic + Viam integration
├── d2.html           # Browser dashboard (open locally)
├── leaderboard.json  # Auto-generated, persistent scores
└── README.md
```

---

## Clinical References

- Kosinski, R.J. (2008). A literature review on reaction time.
- Jensen, A.R. (2006). *Clocking the Mind*. Elsevier.
- Ratcliff, R. & McKoon, G. (2008). The diffusion decision model. *Psychological Review*.
- Hultsch, D.F. et al. (2002). Intraindividual variability in cognitive performance. *Neuropsychology*.
- Fozard, J.L. et al. Normative Aging Study — longitudinal CRT slowing data.
- TILDA (2020). Choice reaction time and mobility decline. *eClinicalMedicine*.
- Luce, R.D. (1986). *Response Times*. Oxford University Press.

---

## Built At

**YHack 2026** — 24-hour hackathon  
Team project · Cornell University

---

## Disclaimer

Reflex Rush is a research and demonstration tool. It is **not a medical device** and does not provide clinical diagnoses. Results are informational only and should not be used to make medical decisions.
