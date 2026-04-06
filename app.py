import requests
import telebot
import time
import os
import threading
import math
from flask import Flask
from datetime import datetime, timezone, timedelta

# ====================== FLASK APP ======================
app = Flask(__name__)

@app.route('/')
def home():
    return "🤖 APEX-ENGINE v3.0 - STABLE CORE", 200

@app.route('/ping', methods=['GET', 'HEAD'])
def ping():
    return "pong", 200

# ====================== CONFIG ======================
print("🚀 APEX-ENGINE v3.0 - STABLE CORE", flush=True)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
API_KEY = os.environ.get("API_KEY")

bot = None
if not all([BOT_TOKEN, CHAT_ID, API_KEY]):
    print("❌ ERREUR: Variables manquantes.", flush=True)
else:
    try:
        bot = telebot.TeleBot(BOT_TOKEN)
        print("✅ Bot Telegram OK", flush=True)
    except Exception as e:
        print(f"❌ Erreur Telegram: {e}", flush=True)

BASE_URL = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": API_KEY}

# ====================== SESSION MANAGEMENT ======================
SESSION_DURATION = 6 * 60 * 60  # 6 heures
session_start = time.time()

# Anti-duplicate intelligent
sent_alerts = {}

# ====================== CONSTANTS ======================
MAX_BETS_PER_SESSION = 10
RHO = 0.10

# ====================== BLACKLIST ======================
BLACKLIST_KEYWORDS = [
    "u17", "u18", "u19", "u20", "u21", "u23", 
    "ii", " b ", "reserves", "youth", "women", "womens", "femenil", "amateur",
    " w", "(w)"
]

# ====================== MATH ENGINE ======================
def poisson_prob(lmbda, k):
    try:
        return (math.exp(-lmbda) * (lmbda ** k)) / math.factorial(k)
    except:
        return 0.0

def calculate_match_probabilities(hxg, axg):
    probs = {"H": 0.0, "D": 0.0, "A": 0.0}
    if hxg <= 0.1 or axg <= 0.1:
        return probs

    hp = [poisson_prob(hxg, i) for i in range(7)]
    ap = [poisson_prob(axg, i) for i in range(7)]

    for h in range(7):
        for a in range(7):
            p = hp[h] * ap[a]

            if h == 0 and a == 0: p *= (1 - RHO)
            elif h == 1 and a == 0: p *= (1 + RHO)
            elif h == 0 and a == 1: p *= (1 + RHO)
            elif h == 1 and a == 1: p *= (1 - RHO)

            if h > a: probs["H"] += p
            elif h == a: probs["D"] += p
            else: probs["A"] += p

    return probs

def calibrate_probability(p):
    return 0.85 * p

# ====================== VALUE DETECTOR ======================
def detect_value(probs, odds_data, hxg, axg):
    if not odds_data:
        return []

    best = {"Home": (0, ""), "Draw": (0, ""), "Away": (0, "")}

    for bm_data in odds_data:
        for bm in bm_data.get('bookmakers', []):
            for bet in bm['bets']:
                if bet['name'] == "Match Winner":
                    for v in bet['values']:
                        odd = float(v['odd'])
                        side = v['value']
                        if odd > best[side][0]:
                            best[side] = (odd, bm['name'])

    opps = []

    for key, side in [("H", "Home"), ("D", "Draw"), ("A", "Away")]:
        prob = calibrate_probability(probs[key])
        odd, bookie = best[side]

        if odd < 1.50 or odd > 12.0:
            continue

        if prob < 0.15:
            continue

        # ROI dynamique
        roi = (prob * odd) - 1.0

        if odd < 3.0 and roi < 0.04:
            continue
        elif odd < 6.0 and roi < 0.06:
            continue
        elif roi < 0.10:
            continue

        opps.append({
            "label": side.upper(),
            "odd": odd,
            "roi": roi,
            "prob": prob
        })

    if opps:
        return [sorted(opps, key=lambda x: x['roi'], reverse=True)[0]]

    return []

# ====================== HELPERS ======================
def check_blacklist_teams(home, away):
    h = home.lower()
    a = away.lower()
    return any(kw in h or kw in a for kw in BLACKLIST_KEYWORDS)

def safe_api_call(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code == 200:
            return r.json()
    except:
        pass
    return None

def get_fixtures():
    data = safe_api_call(f"{BASE_URL}/fixtures?date={time.strftime('%Y-%m-%d')}")
    return data.get('response', []) if data else []

def get_team_stats(tid, lid, season):
    data = safe_api_call(f"{BASE_URL}/teams/statistics?team={tid}&league={lid}&season={season}")
    return data.get('response') if data else None

def get_odds(fid):
    data = safe_api_call(f"{BASE_URL}/odds?fixture={fid}")
    return data.get('response', []) if data else []

# ====================== NOTIFICATION ======================
def notify(opp, info, hxg, axg):
    if not bot:
        return

    fid = info['id']
    now_ts = time.time()

    if fid in sent_alerts and now_ts - sent_alerts[fid] < SESSION_DURATION:
        return

    sent_alerts[fid] = now_ts

    sel = info['home'] if opp['label'] == "HOME" else info['away'] if opp['label'] == "AWAY" else "Draw"

    msg = (
        f"⚽ {info['home']} vs {info['away']}\n"
        f"🌍 {info['league']}\n\n"
        f"📊 xG: {hxg:.2f} - {axg:.2f}\n"
        f"🚨 BET\nSelection: {sel}\n"
        f"Cote: {opp['odd']:.2f}\n"
        f"ROI: +{opp['roi']*100:.1f}%"
    )

    try:
        bot.send_message(CHAT_ID, msg)
        print(f"✅ SENT: {sel} @ {opp['odd']:.2f}", flush=True)
    except:
        pass

# ====================== MAIN ======================
def check_value_bets():
    global session_start

    print("\n⏰ Check v3.0", flush=True)

    # RESET SESSION
    if time.time() - session_start > SESSION_DURATION:
        sent_alerts.clear()
        session_start = time.time()
        print("🔄 RESET SESSION", flush=True)

    fixtures = get_fixtures()
    if not fixtures:
        return

    now = datetime.now(timezone.utc)

    for f in fixtures[:120]:
        try:
            m_date = datetime.fromisoformat(f['fixture']['date'].replace('Z', '+00:00'))
            if not (timedelta(minutes=0) < (m_date - now) < timedelta(hours=6)):
                continue
        except:
            continue

        home = f['teams']['home']['name']
        away = f['teams']['away']['name']

        if check_blacklist_teams(home, away):
            continue

        fid = f['fixture']['id']

        s_home = get_team_stats(f['teams']['home']['id'], f['league']['id'], f['league']['season'])
        s_away = get_team_stats(f['teams']['away']['id'], f['league']['id'], f['league']['season'])

        if not s_home or not s_away:
            continue

        try:
            # Attaque domicile
            h_xg = s_home['goals']['for']['total']['home'] / max(1, s_home['fixtures']['played']['home'])

            # Attaque extérieur
            a_xg = s_away['goals']['for']['total']['away'] / max(1, s_away['fixtures']['played']['away'])

            # Défense
            h_conc = s_home['goals']['against']['total']['total'] / max(1, s_home['fixtures']['played']['total'])
            a_conc = s_away['goals']['against']['total']['total'] / max(1, s_away['fixtures']['played']['total'])

            # xG final corrigé
            hxg = ((h_xg + a_conc) / 2) * 1.10
            axg = (a_xg + h_conc) / 2

        except:
            continue

        odds = get_odds(fid)
        probs = calculate_match_probabilities(hxg, axg)

        opps = detect_value(probs, odds, hxg, axg)

        if opps:
            notify(opps[0], {
                "id": fid,
                "league": f['league']['name'],
                "home": home,
                "away": away
            }, hxg, axg)

        time.sleep(0.25)

# ====================== LOOP ======================
def run_loop():
    print("🗓️ Loop started.", flush=True)
    while True:
        try:
            check_value_bets()
        except Exception as e:
            print(f"❌ Loop Error: {e}", flush=True)
        time.sleep(900)

if bot:
    threading.Thread(target=run_loop, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
