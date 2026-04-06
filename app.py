import requests
import telebot
import time
import schedule
import os
import threading
import math
import csv
from flask import Flask
from datetime import datetime, timezone, timedelta

print("🚀 APEX-SIRIUS v4.0 - Multi-marchés + Risk + Tracking", flush=True)

# ====================== FLASK ======================
app = Flask(__name__)

@app.route('/')
def home():
    return "🤖 APEX-SIRIUS v4.0 - CORE STABLE", 200

@app.route('/ping')
def ping():
    return "pong", 200

# ====================== CONFIG ======================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
API_KEY = os.environ.get("API_KEY")

bot = None
if all([BOT_TOKEN, CHAT_ID, API_KEY]):
    try:
        bot = telebot.TeleBot(BOT_TOKEN)
        print("✅ Telegram Bot initialisé", flush=True)
    except Exception as e:
        print(f"❌ Erreur Telegram: {e}", flush=True)
else:
    print("❌ Variables d'environnement manquantes", flush=True)

BASE_URL = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": API_KEY}

# ====================== TRACKING ======================
TRACKING_FILE = "/tmp/apex_tracking.csv"

def log_bet(match, market, side, odd, prob, edge, stake):
    try:
        with open(TRACKING_FILE, "a", newline='') as f:
            writer = csv.writer(f)
            writer.writerow([datetime.now().isoformat(), match, market, side, odd, prob, edge, stake])
    except:
        pass

# ====================== MATH ======================
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
            if h == a == 0: p *= 0.9
            elif h == a == 1: p *= 1.1
            if h > a: probs["H"] += p
            elif h == a: probs["D"] += p
            else: probs["A"] += p

    total = sum(probs.values())
    if total > 0:
        probs = {k: v / total for k, v in probs.items()}
    return probs

def derive_markets(hxg, axg):
    probs = calculate_match_probabilities(hxg, axg)
    total_goals = hxg + axg
    over25 = 1 - sum(poisson_prob(total_goals, k) for k in range(3))
    btts = (1 - poisson_prob(hxg, 0)) * (1 - poisson_prob(axg, 0))
    return {"1X2": probs, "O2.5": over25, "BTTS": btts}

# ====================== VALUE + RISK ======================
def compute_edge(prob, odd):
    return prob - (1 / odd)

def kelly_fraction(prob, odd, fraction=0.5):
    if odd <= 1:
        return 0
    return max((prob * odd - 1) / (odd - 1), 0) * fraction

def detect_value(markets, odds_data):
    opportunities = []

    # 1X2
    for side_key, side_name in [("H", "Home"), ("D", "Draw"), ("A", "Away")]:
        prob = markets["1X2"][side_key]
        for bm_data in odds_data:
            for bm in bm_data.get('bookmakers', []):
                for bet in bm.get('bets', []):
                    if bet['name'] == "Match Winner":
                        for v in bet.get('values', []):
                            if v['value'] == side_name:
                                odd = float(v['odd'])
                                edge = compute_edge(prob, odd)
                                if edge > 0.04:
                                    opportunities.append({
                                        "market": "1X2",
                                        "side": side_name,
                                        "prob": prob,
                                        "odd": odd,
                                        "edge": edge,
                                        "stake": kelly_fraction(prob, odd)
                                    })

    # Over 2.5 & BTTS
    for mkt, prob in [("O2.5", markets["O2.5"]), ("BTTS", markets["BTTS"])]:
        for bm_data in odds_data:
            for bm in bm_data.get('bookmakers', []):
                for bet in bm.get('bets', []):
                    if (mkt == "O2.5" and bet['name'] == "Goals Over/Under") or \
                       (mkt == "BTTS" and bet['name'] == "Both Teams To Score"):
                        for v in bet.get('values', []):
                            if (mkt == "O2.5" and v['value'] == 'Over 2.5') or \
                               (mkt == "BTTS" and v['value'] == 'Yes'):
                                odd = float(v['odd'])
                                edge = compute_edge(prob, odd)
                                if edge > 0.04:
                                    opportunities.append({
                                        "market": mkt,
                                        "side": "Yes",
                                        "prob": prob,
                                        "odd": odd,
                                        "edge": edge,
                                        "stake": kelly_fraction(prob, odd)
                                    })

    opportunities.sort(key=lambda x: x['edge'], reverse=True)
    return opportunities[:3]

# ====================== FEATURE ENGINE ======================
def build_features(fixture):
    try:
        hxg = 1.45
        axg = 1.25
        return hxg, axg
    except:
        return 1.3, 1.3

# ====================== API HELPERS ======================
def safe_api_call(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code == 200:
            return r.json()
    except:
        pass
    return None

def get_fixtures():
    today = time.strftime("%Y-%m-%d")
    data = safe_api_call(f"{BASE_URL}/fixtures?date={today}")
    return data.get('response', []) if data else []

def get_odds(fid):
    data = safe_api_call(f"{BASE_URL}/odds?fixture={fid}")
    return data.get('response', []) if data else []

# ====================== CHECK ======================
def check_value_bets():
    print(f"\n⏰ Check v4.0 à {datetime.now().strftime('%H:%M:%S')}", flush=True)

    fixtures = get_fixtures()
    print(f"📊 {len(fixtures)} matchs chargés", flush=True)

    now = datetime.now(timezone.utc)
    count_value = 0

    for f in fixtures[:40]:
        status = f['fixture']['status']['short']
        if status in ['FT', 'AET', 'PEN', 'CANC', 'PST', 'ABD']:
            continue

        try:
            match_date = datetime.fromisoformat(f['fixture']['date'].replace('Z', '+00:00'))
            if match_date < now - timedelta(minutes=90) or match_date > now + timedelta(hours=6):
                continue
        except:
            continue

        home = f['teams']['home']['name']
        away = f['teams']['away']['name']
        league_name = f['league']['name']
        country = f['league'].get('country', 'N/A')
        date_time = f['fixture']['date'][:16].replace('T', ' ')

        hxg, axg = build_features(f)
        markets = derive_markets(hxg, axg)
        odds_data = get_odds(f['fixture']['id'])

        opportunities = detect_value(markets, odds_data)

        for opp in opportunities:
            count_value += 1
            stake = opp['stake'] * 100
            msg = f"""🚨 APEX v4.0 VALUE BET

🌍 {country} | 🏆 {league_name}
🕒 {date_time}

{home} vs {away}
🎯 {opp['market']} - {opp['side']}
💰 Cote : {opp['odd']:.2f}
📈 Prob : {opp['prob']:.1%}
⚡ Edge : +{opp['edge']*100:.1f}%
💵 Stake suggéré : {stake:.1f}%"""

            try:
                bot.send_message(CHAT_ID, msg)
                print(f"✅ Sent : {opp['market']} {opp['side']} @ {opp['odd']:.2f}", flush=True)
                log_bet(f"{home} vs {away}", opp['market'], opp['side'], opp['odd'], opp['prob'], opp['edge'], stake)
            except:
                pass

    print(f"🔍 {count_value} value bets envoyés", flush=True)

# ====================== SCHEDULER ======================
def run_scheduler():
    print("🗓️ Scheduler démarré (30 min)", flush=True)
    time.sleep(10)
    check_value_bets()
    schedule.every(30).minutes.do(check_value_bets)
    while True:
        schedule.run_pending()
        time.sleep(1)

if bot:
    threading.Thread(target=run_scheduler, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
