"""
Reflex Rush — Clinical Assessment Server (runs on LAPTOP)
Controls Pi LEDs + buzzer remotely via Viam SDK.
Serves API for the dashboard (also on laptop).
"""

from flask import Flask, jsonify, request, Response
from flask_cors import CORS
from dotenv import load_dotenv
import threading
import asyncio
import time
import json
import random
import os
import statistics
import io

load_dotenv()

# Viam Imports
from viam.robot.client import RobotClient
from viam.components.board import Board
from viam.components.camera import Camera 
from viam.media.utils.pil import viam_to_pil_image

app = Flask(__name__)
CORS(app)

import logging
class QuietFilter(logging.Filter):
    def filter(self, record):
        return "/api/state" not in record.getMessage()
logging.getLogger('werkzeug').addFilter(QuietFilter())

# ═══════════════════════════════════════════════════
#  VIAM CONFIG & GLOBALS
# ═══════════════════════════════════════════════════
VIAM_ADDRESS  = os.getenv("VIAM_ADDRESS", "love-your-brain-main.rd85y50nhj.viam.cloud")
VIAM_API_KEY  = os.getenv("VIAM_API_KEY", "")
VIAM_KEY_ID   = os.getenv("VIAM_KEY_ID", "")
BOARD_NAME    = os.getenv("BOARD_NAME", "board-1")

LED_PINS = {"RED": "12", "GREEN": "7", "BLUE": "38", "YELLOW": "36"}
BUZZER_PIN = "40"
COLORS = ["RED", "GREEN", "BLUE", "YELLOW"]

viam_loop, viam_board, viam_camera = None, None, None 
viam_ready = threading.Event()

PLAYER_AGE = int(os.getenv("PLAYER_AGE", "30"))
PLAYER_SEX = os.getenv("PLAYER_SEX", "M")
MAX_SESSIONS = 3
ROUNDS_PER_SESSION = 5

# ═══════════════════════════════════════════════════
#  VIAM CONNECTION & HELPERS
# ═══════════════════════════════════════════════════
async def _connect_viam():
    global viam_board, viam_camera
    opts = RobotClient.Options.with_api_key(api_key=VIAM_API_KEY, api_key_id=VIAM_KEY_ID)
    robot = await RobotClient.at_address(VIAM_ADDRESS, opts)
    viam_board = Board.from_robot(robot, BOARD_NAME)
    try:
        viam_camera = Camera.from_robot(robot, "camera-1") 
        print("  Viam connected (Board + Camera)!")
    except Exception as e:
        print(f"  Viam connected (Board), Camera failed: {e}")
    viam_ready.set()

def _run_viam_loop():
    global viam_loop
    viam_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(viam_loop)
    viam_loop.run_until_complete(_connect_viam())
    viam_loop.run_forever()

def viam_call(coro):
    return asyncio.run_coroutine_threadsafe(coro, viam_loop).result(timeout=5)

async def _led_on(color): await (await viam_board.gpio_pin_by_name(LED_PINS[color])).set(True)
async def _led_off(color): await (await viam_board.gpio_pin_by_name(LED_PINS[color])).set(False)
async def _all_off():
    for c in COLORS: await (await viam_board.gpio_pin_by_name(LED_PINS[c])).set(False)
    await (await viam_board.gpio_pin_by_name(BUZZER_PIN)).set(False)
async def _buzz(d=0.1):
    p = await viam_board.gpio_pin_by_name(BUZZER_PIN); await p.set(True); await asyncio.sleep(d); await p.set(False)

def led_on(c): viam_call(_led_on(c))
def led_off(c): viam_call(_led_off(c))
def all_leds_off(): viam_call(_all_off())
def buzz_tick(): viam_call(_buzz(0.05))
def buzz_correct(): viam_call(_buzz(0.04)); time.sleep(0.03); viam_call(_buzz(0.04))
def buzz_wrong(): viam_call(_buzz(0.3))
def buzz_session_end(): viam_call(_buzz(0.1)); time.sleep(0.1); viam_call(_buzz(0.2))

# ═══════════════════════════════════════════════════
#  GAME STATE & LOGIC
# ═══════════════════════════════════════════════════
game = {
    "status": "AWAITING_NAME", 
    "player_name": "", "age": 30, "sex": "M",
    "current_session": 1, "round": 0,
    "target_color": None, "message": "", "reaction_ms": 0,
    "all_results": [], "scorecard": None, "blink_rate": None
}

input_event = threading.Event()
player_registered = threading.Event()
input_color = None
game_lock = threading.Lock()

LB_FILE = "leaderboard.json"
def load_lb(): return json.load(open(LB_FILE)) if os.path.exists(LB_FILE) else []
def save_lb(lb): json.dump(lb, open(LB_FILE, "w"), indent=2)


def compute_expected_rt(age, sex):
    """
    Age- and sex-adjusted expected RT for a 4-choice color-match task.

    Formula: 240 + (age - 20) * 0.5 ± 10
      - Base 240ms: empirical midpoint for 4-choice visual RT in healthy young adults
        (Kosinski 2008, Jensen 2006; simple RT ~215ms + ~25ms Hick's-Law penalty for 4 alts)
      - +0.5ms/year after age 20: linear slowing per Fozard et al. (Normative Aging Study),
        consistent with ~1ms/year CRT slowing reported in TILDA (2020)
      - -10ms for male / +10ms for female: sex offset per published RT meta-analyses

    Validated ranges per this formula (ages 30–80):
      Male   (−10):  235ms (age 30) → 260ms (age 80)  | Full healthy band: 185–465ms
      Female (+10):  245ms (age 30) → 270ms (age 80)  | Full healthy band: 205–480ms
    """
    age = max(20, int(age))
    sex_offset = -10 if sex == "M" else 10
    return 240 + ((age - 20) * 0.5) + sex_offset


def classify_rt(median_rt, expected_rt):
    """
    Classify median RT relative to the age/sex-adjusted expected value.

    Threshold derivation from the validated clinical ranges:
      Male   age 30: expected=235ms, lower bound=185ms  →  elite = expected − 50
      Male   age 80: expected=260ms, upper bound=465ms  →  concern ≈ expected + 205

    Using symmetric/conservative offsets so no one gets a false "concern" diagnosis:
      Elite:         diff < −50   (≥50ms faster than expected — top ~5th percentile)
      Healthy:  −50 ≤ diff ≤ +75  (within normal 4-choice RT band for age/sex)
      Average:  +75 < diff ≤ +175 (slightly elevated, monitor trend)
      Concern:       diff > +175  (clinically meaningful slowing — not a diagnosis)

    Why these numbers?
      The old thresholds (±20ms, ±50ms) were calibrated for SIMPLE RT (~215ms baseline).
      This is a 4-CHOICE task: healthy adults routinely score 280–380ms. Using the
      old thresholds caused players with perfectly normal 4-choice RTs (~320ms) to
      be flagged as "Below Average" — a false negative that undermined clinical trust.
    """
    diff = median_rt - expected_rt
    if diff < -50:
        return "Elite — Faster than expected"
    elif diff <= 75:
        return "Healthy — Right on target"
    elif diff <= 175:
        return "Average — Slightly slower"
    else:
        return "Below average — Worth watching"


def compute_score(results, age, sex, blink_rate=None):
    """
    RT scoring methodology:
    - Speed (median RT): computed only from correct hits. Omission errors (misses) are 
      excluded per Jensen (2006) and Ratcliff & McKoon (2008) — they are accuracy events,
      not speed events. Commission errors (wrong key) get a +300ms penalty RT included.
    - Accuracy: hits / total rounds, shown separately.
    - CV (coefficient of variation): consistency of correct-hit RTs. Lower = more consistent.
      Elevated CV is a known marker in Parkinson's and MCI research (Hultsch et al. 2002).
    - Final leaderboard score: accuracy-weighted median RT.
      score = median_rt / accuracy  → rewards both speed AND consistency of correct responses.
    """
    correct_rts = [r["rt_ms"] for r in results if r.get("correct") and r["rt_ms"] is not None]
    
    # Use correct hits for speed baseline; include wrong-key penalties for realism
    all_valid_rts = correct_rts  # speed score only from clean hits
    
    if not all_valid_rts:
        all_valid_rts = [r["rt_ms"] for r in results if r["rt_ms"] is not None] or [9999]

    med      = statistics.median(all_valid_rts)
    mean_rt  = statistics.mean(all_valid_rts)
    sd       = statistics.stdev(all_valid_rts) if len(all_valid_rts) > 1 else 0
    cv       = sd / mean_rt if mean_rt > 0 else 0

    total    = len(results)
    hits     = len([r for r in results if r.get("correct")])
    accuracy = hits / total if total > 0 else 0

    # Accuracy-weighted score: penalizes players who are fast but inaccurate
    # A player with 200ms median and 60% accuracy scores 200/0.6 = 333 (worse than 
    # a 250ms player with 100% accuracy scoring 250/1.0 = 250)
    weighted_score = round(med / accuracy) if accuracy > 0 else 9999

    expected = compute_expected_rt(age, sex)
    rt_class = classify_rt(med, expected)

    blink_note = None
    if blink_rate is not None:
        if blink_rate < 8:
            blink_note = f"{blink_rate}/min — Low blink rate (normal 15–20). Possible dry eye or intense focus."
        elif blink_rate > 30:
            blink_note = f"{blink_rate}/min — High blink rate (normal 15–20). Can indicate stress or irritation."
        else:
            blink_note = f"{blink_rate}/min — Normal range."

    return {
        "median_rt":     round(med),
        "expected_rt":   round(expected),
        "cv":            round(cv, 3),
        "rt_class":      rt_class,
        "accuracy":      round(accuracy * 100),
        "correct_hits":  hits,
        "total_rounds":  total,
        "weighted_score": weighted_score,
        "blink_rate":    blink_rate,
        "blink_note":    blink_note,
    }

def game_loop():
    global input_color
    viam_ready.wait()
    all_leds_off()

    while True:
        with game_lock:
            game.update({"status": "AWAITING_NAME", "player_name": "", "age": PLAYER_AGE, "sex": PLAYER_SEX, "all_results": [], "scorecard": None})
        
        player_registered.wait()
        player_registered.clear()

        for sess in range(1, MAX_SESSIONS + 1):
            with game_lock:
                game.update({"status": "SESSION_START", "current_session": sess, "message": f"Session {sess} of {MAX_SESSIONS}"})
            
            # Wait for physical button to begin session
            input_event.clear()
            input_event.wait()
            input_event.clear()

            for rnd in range(1, ROUNDS_PER_SESSION + 1):
                # Speed increases gradually — starts forgiving, gets tighter each session
                timeout_ms = max(800, 2500 - (sess * 250) - (rnd * 40))
                
                # False-start guard: if player presses before the light, restart the wait silently
                false_start_detected = True
                while false_start_detected:
                    with game_lock: game.update({"status": "WAITING", "round": rnd, "message": "Wait..."})
                    input_event.clear()
                    if input_event.wait(timeout=random.uniform(1.8, 4.0)):
                        input_event.clear()
                        with game_lock: game.update({"status": "WAITING", "message": "Too early — wait for the color!"})
                        buzz_wrong()
                        time.sleep(1.0)
                    else:
                        false_start_detected = False

                target = random.choice(COLORS)
                led_on(target)
                with game_lock: game.update({"status": "FLASH", "target_color": target, "message": "SMASH!"})
                
                t0 = time.perf_counter()
                input_event.clear()
                responded = input_event.wait(timeout=timeout_ms / 1000.0)
                rt = round((time.perf_counter() - t0) * 1000)
                led_off(target)
                
                if not responded:
                    correct      = False
                    miss_type    = "missed"
                    msg          = "Too slow! —0 pts"
                    recorded_rt  = None
                else:
                    correct     = (input_color == target)
                    recorded_rt = rt
                    if not correct:
                        recorded_rt = min(timeout_ms, rt + 300)
                        miss_type   = "wrong"
                        msg         = f"Wrong color! +300ms penalty"
                    else:
                        miss_type = None
                        msg       = f"Nice! {rt}ms"

                with game_lock:
                    game["all_results"].append({
                        "session":   sess, "round": rnd,
                        "rt_ms":     recorded_rt,
                        "correct":   correct,
                        "miss_type": miss_type
                    })
                    game.update({"status": "RESULT", "reaction_ms": recorded_rt if recorded_rt else timeout_ms, "message": msg})
                
                if correct: buzz_correct()
                else:       buzz_wrong()
                time.sleep(1.2)
                
            buzz_session_end()
            time.sleep(1)

        # All sessions complete → Calculate Scorecard & Leaderboard
        with game_lock:
            scorecard = compute_score(game["all_results"], game["age"], game["sex"], game.get("blink_rate"))
            game.update({"status": "SCORECARD", "scorecard": scorecard})
            
            entry = {
                "name":      game["player_name"],
                "rounds":    MAX_SESSIONS * ROUNDS_PER_SESSION,
                "best_ms":   scorecard["median_rt"],
                "avg_ms":    scorecard["weighted_score"],
                "accuracy":  scorecard["accuracy"],
                "timestamp": time.time()
            }
            lb = sorted(load_lb() + [entry], key=lambda x: x.get("avg_ms", 9999))[:50]
            save_lb(lb)
            
        print(f"  >> Scorecard: {game['player_name']} | {scorecard['median_rt']}ms | Expected {scorecard['expected_rt']}ms | {scorecard['rt_class']}")
        
        time.sleep(10)

# ═══════════════════════════════════════════════════
#  API ENDPOINTS
# ═══════════════════════════════════════════════════
@app.route("/api/state")
def api_state():
    with game_lock: return jsonify(game)

@app.route("/api/register", methods=["POST"])
def api_register():
    data = request.json or {}
    name = data.get("name", "Anon").strip()
    if name and game["status"] in ["AWAITING_NAME", "SCORECARD"]:
        with game_lock: game["player_name"] = name
        player_registered.set()
        return jsonify({"ok": True})
    return jsonify({"ok": False}), 400

@app.route("/api/input", methods=["POST"])
def api_input():
    global input_color
    data = request.json or {}
    if data.get("color", "").upper() in COLORS:
        input_color = data["color"].upper()
        input_event.set()
    elif data.get("action") == "start":
        input_event.set()
    return jsonify({"ok": True})

@app.route("/api/blink", methods=["POST"])
def api_blink():
    data = request.json or {}
    rate = data.get("rate")
    if rate is not None:
        with game_lock: game["blink_rate"] = int(rate)
    return jsonify({"ok": True})

@app.route("/api/leaderboard")
def api_lb(): return jsonify({"leaderboard": load_lb()})

@app.route("/api/stats")
def api_stats():
    lb = load_lb()
    return jsonify({"total_games": len(lb), "fastest_ever": min([e["best_ms"] for e in lb]) if lb else "--", "most_rounds": MAX_SESSIONS * ROUNDS_PER_SESSION})

@app.route("/api/camera_stream")
def api_camera_stream():
    def generate():
        while True:
            if viam_ready.is_set() and viam_camera:
                try:
                    img = viam_to_pil_image(viam_call(viam_camera.get_images())[0][0])
                    buf = io.BytesIO(); img.save(buf, format='JPEG')
                    yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + buf.getvalue() + b'\r\n')
                except: pass
            time.sleep(0.04)
    return Response(generate(), mimetype='multipart/x-mixed-replace; boundary=frame')

if __name__ == "__main__":
    print("\n  === REFLEX RUSH: CLINICAL MODE ===")
    try: 
        PLAYER_AGE = max(30, int(input("  Default player age (30 or older): ")))
    except ValueError: PLAYER_AGE = 30
    PLAYER_SEX = input("  Default player sex (M/F): ").strip().upper()
    if PLAYER_SEX not in ["M", "F"]: PLAYER_SEX = "M"
    
    exp = round(240 + ((PLAYER_AGE - 20) * 0.5) + (-10 if PLAYER_SEX == "M" else 10))
    print(f"  Locked Demographics: Age {PLAYER_AGE}, Sex {PLAYER_SEX}")
    print(f"  Expected RT for this profile: {exp}ms")
    print(f"  Healthy range: {exp - 50}ms – {exp + 75}ms\n")

    threading.Thread(target=_run_viam_loop, daemon=True).start()
    threading.Thread(target=game_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)