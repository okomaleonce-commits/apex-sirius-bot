import requests
import telebot
import time
import schedule
import os
import threading
import math
from flask import Flask
from datetime import datetime, timezone, timedelta

print("🚀 APEX-SIRIUS v4.7 - NO SPAM EDITION", flush=True)

# ====================== FLASK ======================
app = Flask(__name__)

@app.route('/')
def home():
    return "🤖 APEX-SIRIUS v4.7 Running", 200

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
    print("❌ Variables manquantes", flush=True)

BASE_URL = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": API_KEY}

BLACKLIST_NAMES = ["women", " w", "(w)", "u17", "u19", "u21", "reserves", " b ", "ii"]

# ====================== MATH ======================
def poisson_prob(lmbda, k):
    try: return (math.exp(-lmbda) * (lmbda ** k)) / math.factorial(k)
    except: return 0.0

def calculate_match_probabilities(hxg, axg):
    probs = {"H": 0.0, "D": 0.0, "A": 0.0}
    if hxg <= 0.1 or axg <= 0.1: return probs
    hp = [poisson_prob(hxg, i) for i in range(7)]
    ap = [poisson_prob(axg, i) for i in range(7)]
    for h in range(7):
        for a in range(7):
            p = hp[h] * ap[a]
            if h == 0 and a == 0: p *= 0.90
            elif h == 1 and a == 0: p *= 1.10
            elif h == 0 and a == 1: p *= 1.10
            elif h == 1 and a == 1: p *= 0.90
            if h > a: probs["H"] += p
            elif h == a: probs["D"] += p
            else: probs["A"] += p
    total = sum(probs.values())
    if total > 0: probs = {k: v / total for k, v in probs.items()}
    return probs

def derive_markets(hxg, axg):
    probs = calculate_match_probabilities(hxg, axg)
    total_goals = hxg + axg
    over25 = 1 - sum(poisson_prob(total_goals, k) for k in range(3))
    return {"1X2": probs, "O2.5": over25}

# ====================== VALUE (NO SPAM LOGIC) ======================
def compute_edge(prob, odd):
    return prob - (1 / odd)

def detect_value_best_odds(markets, odds_data):
    # Dictionnaire pour stocker la meilleure cote trouvée pour chaque issue
    # Format: { "Home": {"odd": 0, "bookie": ""}, "Draw": {...}, "Away": {...} }
    best_market_odds = {
        "Home": {"odd": 0.0, "bookie": "N/A"},
        "Draw": {"odd": 0.0, "bookie": "N/A"},
        "Away": {"odd": 0.0, "bookie": "N/A"}
    }

    # 1. Scanner tous les bookmakers pour trouver la MEILLEURE cote
    for bm_data in odds_data:
        for bm in bm_data.get('bookmakers', []):
            bookie_name = bm['name']
            for bet in bm.get('bets', []):
                if bet['name'] == "Match Winner":
                    for v in bet.get('values', []):
                        side = v['value'] # "Home", "Draw", "Away"
                        odd = float(v['odd'])
                        # Si cette cote est meilleure que celle en mémoire, on garde
                        if odd > best_market_odds[side]['odd']:
                            best_market_odds[side] = {"odd": odd, "bookie": bookie_name}

    # 2. Calculer l'edge seulement sur les MEILLEURES cotes
    opportunities = []
    for side_key, side_name in [("H", "Home"), ("D", "Draw"), ("A", "Away")]:
        prob = markets["1X2"][side_key]
        best_odd = best_market_odds[side_name]['odd']
        
        if best_odd <= 1.10: continue # Pas de cote valide trouvée
        
        edge = compute_edge(prob, best_odd)
        
        if edge > 0.02:
            opportunities.append({
                "market": "1X2",
                "side": side_name,
                "prob": prob,
                "odd": best_odd,
                "edge": edge,
                "bookie": best_market_odds[side_name]['bookie']
            })

    # Trier par Edge décroissant
    opportunities.sort(key=lambda x: x['edge'], reverse=True)
    return opportunities

# ====================== FEATURES ======================
def safe_api_call(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code == 200: return r.json()
    except: pass
    return None

def get_team_stats(tid, lid, season):
    return safe_api_call(f"{BASE_URL}/teams/statistics?team={tid}&league={lid}&season={season}").get('response')

def build_features(fixture):
    try:
        lid = fixture['league']['id']
        season = fixture['league']['season']
        ht_id = fixture['teams']['home']['id']
        at_id = fixture['teams']['away']['id']
        s_home = get_team_stats(ht_id, lid, season)
        s_away = get_team_stats(at_id, lid, season)
        if not s_home or not s_away: return 1.35, 1.20
        # Home Stats
        h_g = s_home['goals']['for']['total']['total']
        h_p = s_home['fixtures']['played']['total']
        hxg = (h_g / h_p) * 1.15 if h_p > 0 else 1.2
        # Away Stats
        a_g = s_away['goals']['for']['total']['total']
        a_p = s_away['fixtures']['played']['total']
        axg = (a_g / a_p) if a_p > 0 else 1.0
        return hxg, axg
    except: return 1.35, 1.20

# ====================== API ======================
def get_fixtures():
    today = time.strftime("%Y-%m-%d")
    url = f"{BASE_URL}/fixtures?date={today}&timezone=UTC"
    data = safe_api_call(url)
    return data.get('response', []) if data else []

def get_odds(fid):
    return safe_api_call(f"{BASE_URL}/odds?fixture={fid}").get('response', [])

# ====================== CHECK ======================
def check_value_bets():
    print(f"\n⏰ Check v4.7 à {datetime.now().strftime('%H:%M:%S')}", flush=True)

    fixtures = get_fixtures()
    print(f"📊 {len(fixtures)} matchs chargés", flush=True)

    now = datetime.now(timezone.utc)
    count_analyzed = 0
    count_sent = 0
    
    for i, f in enumerate(fixtures):
        try:
            home = f['teams']['home']['name']
            away = f['teams']['away']['name']
            status = f['fixture']['status']['short']
            raw_date = f['fixture']['date']

            # 1. Filtre Blacklist
            if any(x in home.lower() or x in away.lower() for x in BLACKLIST_NAMES):
                continue

            # 2. Filtre Status
            if status not in ["NS", "TBD", "1H", "2H", "HT"]:
                continue

            # 3. Filtre Temps
            m_date = datetime.fromisoformat(raw_date.replace('Z', '+00:00'))
            if m_date.tzinfo is None:
                m_date = m_date.replace(tzinfo=timezone.utc)

            # Matchs entre -2h et +12h
            if not (now - timedelta(hours=2) <= m_date <= now + timedelta(hours=12)):
                continue

            count_analyzed += 1
            
            # Analyse
            hxg, axg = build_features(f)
            markets = derive_markets(hxg, axg)
            odds_data = get_odds(f['fixture']['id'])
            
            # Nouvelle fonction sans spam
            opportunities = detect_value_best_odds(markets, odds_data)

            # On envoie SEULEMENT le meilleur pari du match (le 1er de la liste triée)
            if opportunities:
                opp = opportunities[0] 
                
                # Limite globale pour ne pas spammer Telegram
                if count_sent >= 10:
                     print("🛑 Limite de 10 alerts atteinte pour ce tour.", flush=True)
                     break

                msg = f"""🚨 APEX v4.7 BET
{home} vs {away}
🎯 {opp['market']} - {opp['side']}
💰 Best Odd: {opp['odd']:.2f} ({opp['bookie']})
📈 Edge: +{opp['edge']*100:.1f}%"""
                try:
                    bot.send_message(CHAT_ID, msg)
                    print(f"✅ Sent : {home} {opp['side']} @ {opp['odd']:.2f}", flush=True)
                    count_sent += 1
                except: pass

        except Exception as e:
            print(f"⚠️ Erreur: {e}", flush=True)
            continue

    print(f"✅ Terminé: {count_analyzed} analysés. {count_sent} alerts envoyés.", flush=True)

# ====================== SCHEDULER ======================
def run_scheduler():
    print("🗓️ Scheduler (15 min)", flush=True)
    time.sleep(10)
    check_value_bets()
    schedule.every(15).minutes.do(check_value_bets)
    while True:
        schedule.run_pending()
        time.sleep(1)

if bot:
    threading.Thread(target=run_scheduler, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
