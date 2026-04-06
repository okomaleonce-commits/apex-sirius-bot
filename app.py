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
    return "🤖 APEX-ENGINE v2.5 - REALITY CHECK", 200

@app.route('/ping', methods=['GET', 'HEAD'])
def ping():
    return "pong", 200

# ====================== CONFIG ======================
print("🚀 APEX-ENGINE v2.5 - CALIBRATED MODEL", flush=True)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
API_KEY = os.environ.get("API_KEY")

FOOTYSTATS_KEY = "b637867a6fca38fd2f388553abf0768840d84ded4b335ce23d97e708b7a502c6"

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
FS_URL = "https://api.footystats.org/v2"

sent_alerts = set()
tracked_bets = []
fs_teams_cache = {}

# ====================== CONSTANTS ======================
RHO = 0.10

TIER_P0 = ["uefa champions league", "uefa europa league", "uefa europa conference league"]
TIER_N1 = ["premier league", "championship", "la liga", "bundesliga", "ligue 1", "serie a", "eredivisie", "liga portugal", "primeira liga", "jupiler pro league"]
TIER_N2 = ["süper lig", "super lig", "russian premier league", "super league 1", "bundesliga autrichienne", "super league suisse", "superliga", "allsvenskan", "eliteserien", "ekstraklasa", "czech first league", "otp bank liga", "liga 1", "hnl"]
TIER_N3 = ["major league soccer", "liga mx", "liga profesional argentina", "brasileirão", "j1 league", "k league 1", "saudi pro league"]

BLACKLIST_KEYWORDS = ["u17", "u18", "u19", "u20", "u21", "u23", "ii", " b ", "reserves", "youth", "women", "womens", "femenil", "amateur"]

# ====================== MATH ENGINE (CALIBRATED) ======================
def poisson_prob(lmbda, k):
    try:
        return (math.exp(-lmbda) * (lmbda ** k)) / math.factorial(k)
    except: return 0.0

def calculate_match_probabilities(hxg, axg):
    probs = {"H": 0.0, "D": 0.0, "A": 0.0}
    if hxg <= 0.1 or axg <= 0.1: return probs

    hp = [poisson_prob(hxg, i) for i in range(7)]
    ap = [poisson_prob(axg, i) for i in range(7)]

    for h in range(7):
        for a in range(7):
            p = hp[h] * ap[a]
            
            # Dixon-Coles minimal
            if h == 0 and a == 0: p *= (1 - RHO)
            elif h == 1 and a == 0: p *= (1 + RHO)
            elif h == 0 and a == 1: p *= (1 + RHO)
            elif h == 1 and a == 1: p *= (1 - RHO)
            
            if h > a: probs["H"] += p
            elif h == a: probs["D"] += p
            else: probs["A"] += p
            
    return probs

def calibrate_probability(p):
    """
    PRIORITÉ 1: Calibration.
    Ajustement vers le bas pour contrer l'over-estimation des outsiders.
    """
    return 0.85 * p

# ====================== VALUE DETECTOR (FIXED) ======================
def detect_value(probs, odds_data, hxg, axg):
    opps = []
    if not odds_data: return []

    best = {"Home": (0, "N/A"), "Draw": (0, "N/A"), "Away": (0, "N/A")}
    for bm_data in odds_data:
        for bm in bm_data.get('bookmakers', []):
            bn = bm['name']
            for bet in bm['bets']:
                if bet['name'] == "Match Winner":
                    for v in bet['values']:
                        odd = float(v['odd'])
                        side = v['value']
                        if odd > best[side][0]:
                            best[side] = (odd, bn)

    markets = [
        {"key": "H", "side": "Home"},
        {"key": "D", "side": "Draw"},
        {"key": "A", "side": "Away"}
    ]

    for m in markets:
        raw_prob = probs[m['key']]
        
        # 1. Calibration
        prob = calibrate_probability(raw_prob)
        
        odd, bookie = best[m['side']]
        
        if odd < 1.50: continue # Cotes trop basses ignorées
        
        # PRIORITÉ 3: Calcul ROI (Espérance de gain)
        # ROI = (Prob * Cote) - 1
        roi = (prob * odd) - 1.0
        
        # PRIORITÉ 2: Cap sur outsiders
        # Si la cote est énorme (>6), il faut une probabilité solide (>20%)
        if odd > 6.00:
            if prob < 0.20:
                continue # Bloque les hallucinations type @13.00, @37.00

        # PRIORITÉ 4: Filtre dur sur ROI
        # On veut au moins 5% de ROI attendu
        if roi < 0.05: continue

        opps.append({
            "type": "1X2", "label": m['side'].upper(), "odd": odd,
            "roi": roi, "prob": prob, "proba_key": m['key'], "bookie": bookie
        })

    # PRIORITÉ 5: Limite par match
    if opps:
        # On garde le bet avec le meilleur ROI
        opps.sort(key=lambda x: x['roi'], reverse=True)
        return [opps[0]]
    
    return []

# ====================== HELPERS & API ======================
# (Identique v2.4 pour la concision)
def get_league_tier(lname, country):
    lname = lname.lower()
    for kw in BLACKLIST_KEYWORDS:
        if kw in lname: return "BLACKLIST"
    if any(x in lname for x in TIER_P0): return "P0"
    if any(x in lname for x in TIER_N1): return "N1"
    if any(x in lname for x in TIER_N2): return "N2"
    if any(x in lname for x in TIER_N3): return "N3"
    return "UNKNOWN"

def safe_api_call(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code == 200: return r.json()
    except: pass
    return None

def get_fixtures():
    return safe_api_call(f"{BASE_URL}/fixtures?date={time.strftime('%Y-%m-%d')}").get('response', [])

def get_team_stats(tid, lid, season):
    return safe_api_call(f"{BASE_URL}/teams/statistics?team={tid}&league={lid}&season={season}").get('response')

def get_odds(fid):
    return safe_api_call(f"{BASE_URL}/odds?fixture={fid}").get('response', [])

# ====================== NOTIFICATION ======================
def notify(opp, info, hxg, axg):
    if not bot: return
    fid = info['id']
    if fid in sent_alerts: return
    sent_alerts.add(fid)

    sel = opp['label']
    if opp['label'] == "HOME": sel = info['home']
    if opp['label'] == "AWAY": sel = info['away']

    msg = f"⚽ {info['home']} vs {info['away']}\n"
    msg += f"🌍 {info['league']}\n\n"
    msg += f"📊 xG: {hxg:.2f} - {axg:.2f}\n"
    msg += f"🚨 BET VALIDÉ\nSelection: {sel}\nCote: {opp['odd']:.2f}\nROI Estimé: +{opp['roi']*100:.1f}%"

    try:
        bot.send_message(CHAT_ID, msg)
        print(f"✅ SENT: {sel} @ {opp['odd']:.2f} (ROI +{opp['roi']*100:.0f}%)", flush=True)
    except: pass

# ====================== MAIN CHECK ======================
def check_value_bets():
    if not API_KEY: return
    print(f"\n⏰ Check v2.5", flush=True)

    fixtures = get_fixtures()
    if not fixtures: return
    
    now = datetime.now(timezone.utc)
    
    for f in fixtures:
        try:
            m_date = datetime.fromisoformat(f['fixture']['date'].replace('Z', '+00:00'))
            if not (timedelta(minutes=0) < (m_date - now) < timedelta(hours=6)): continue
        except: continue

        tier = get_league_tier(f['league']['name'], f['league']['country'])
        if tier == "BLACKLIST" or tier == "UNKNOWN": continue
        
        fid = f['fixture']['id']
        
        s_home = get_team_stats(f['teams']['home']['id'], f['league']['id'], f['league']['season'])
        s_away = get_team_stats(f['teams']['away']['id'], f['league']['id'], f['league']['season'])
        if not s_home or not s_away: continue
        
        # xG Calculation (Simple & Safe)
        try:
            h_avg = s_home['goals']['for']['total']['total'] / s_home['fixtures']['played']['total']
            a_avg = s_away['goals']['for']['total']['total'] / s_away['fixtures']['played']['total']
            hxg = h_avg * 1.15
            axg = a_avg
        except:
            continue

        odds = get_odds(fid)
        probs = calculate_match_probabilities(hxg, axg)
        
        opps = detect_value(probs, odds, hxg, axg)
        
        if opps:
            info = {
                'id': fid, 'league': f['league']['name'], 
                'home': f['teams']['home']['name'], 'away': f['teams']['away']['name']
            }
            notify(opps[0], info, hxg, axg)
        
        time.sleep(0.5)

    print("✅ Check done.", flush=True)

# ====================== LOOP ======================
def run_loop():
    print("🗓️ Loop started.", flush=True)
    while True:
        try: check_value_bets()
        except: pass
        time.sleep(900)

if bot:
    threading.Thread(target=run_loop, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
