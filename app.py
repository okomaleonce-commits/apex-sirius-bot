import requests
import telebot
import time
import schedule
import os
import sys
import threading
import math
from flask import Flask, render_template_string
from datetime import datetime, timezone, timedelta

# ====================== FLASK APP (EN PREMIER) ======================
app = Flask(__name__)

@app.route('/')
def home():
    return "🤖 APEX-ENGINE v1.2 - WHITELIST STRATIFIED", 200

@app.route('/ping', methods=['GET', 'HEAD'])
def ping():
    return "pong", 200

# ====================== CONFIG ======================
print("🚀 APEX-ENGINE v1.2 - INITIALIZING WHITELIST PROTOCOL", flush=True)

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
        print(f"❌ Erreur Telegram init: {e}", flush=True)

BASE_URL = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": API_KEY}

sent_alerts = set()
value_bets_history = []

# ====================== A-LAP CONFIGURATION ======================
LEAGUE_AVG_GOALS = 2.65
HOME_ADVANTAGE = 1.10
RHO = 0.10

# --- WHITELIST & TIERS ---
# Niveau P0: Top Confiance
TIER_P0 = [
    "uefa champions league", "uefa europa league", "uefa europa conference league"
]

# Niveau N1: Top 5 + Premium
TIER_N1 = [
    "premier league", "la liga", "bundesliga", "ligue 1", "serie a", 
    "eredivisie", "liga portugal", "primeira liga", "scottish premiership", 
    "jupiler pro league", "süper lig", "super lig", "eerste divisie",
    "rpl", "premier league russia", "super league greece"
]

# Niveau N2: D2 Fiables
TIER_N2 = [
    "championship", "la liga 2", "laliga smartbank", "2. bundesliga", "ligue 2", 
    "serie b", "liga portugal 2", "scottish championship", "challenger pro league",
    "tff 1. lig", "tweede divisie", "liga 1 romania", "österreichische bundesliga",
    "super league suisse", "superliga denmark", "allsvenskan", "eliteserien", 
    "ekstraklasa", "czech first league", "otp bank liga", "superliga srbija",
    "hnl", "fortuna liga", "first professional league", "premier league ukraine",
    "israeli premier league"
]

# Niveau N3: Conditionnelles (DCS >= 75)
TIER_N3 = [
    "league one", "league two", "national league", "primera rfef", "3. liga",
    "national france", "serie c", "liga 3", "liga profesional argentina",
    "serie a brazil", "liga mx", "major league soccer", "j1 league", "k league 1",
    "saudi pro league", "arabian gulf league", "a-league", "premier soccer league",
    "ligue professionnelle 1", "mtn ligue 1"
]

# --- BLACKLIST ---
BLACKLIST_COUNTRIES = [
    "jordan", "indonesia", "vietnam", "kazakhstan", "azerbaijan", "georgia", 
    "armenia", "belarus", "moldova", "kosovo", "malta", "cyprus", "faroe islands", 
    "gibraltar", "san marino", "liechtenstein"
    # Note: On filtre par pays pour couvrir les ligues non listées
]

BLACKLIST_KEYWORDS = [
    "u17", "u18", "u19", "u20", "u21", "u23", "ii", " b", "reserves", "youth", 
    "primavera", "jong", "amateur", "development", "academy", "women", "womens"
]

# --- SEUILS PAR NIVEAU ---
DCS_MIN_TIERS = { "P0": 65, "N1": 65, "N2": 70, "N3": 75 }
MARGE_MAX_TIERS = { "P0": 0.07, "N1": 0.09, "N2": 0.11, "N3": 0.12 }
EDGE_MIN_TIERS  = { "P0": 0.05, "N1": 0.05, "N2": 0.05, "N3": 0.06 } # 6% pour N3

COTE_MIN = 1.40
COTE_MAX = 8.00

# ====================== HELPER FUNCTIONS ======================

def get_league_tier(league_name, country):
    """Identifie le tier de la ligue"""
    lname = league_name.lower()
    
    # 1. Check Blacklist Mots Clés
    for kw in BLACKLIST_KEYWORDS:
        if kw in lname:
            return "BLACKLIST"
            
    # 2. Check Blacklist Pays (si pas dans Whitelist explicite)
    # Note: on autorise CIV et Tunisie si dans TIER_N3
    if country.lower() in BLACKLIST_COUNTRIES:
        return "BLACKLIST"

    # 3. Check Whitelist
    if any(x in lname for x in TIER_P0): return "P0"
    if any(x in lname for x in TIER_N1): return "N1"
    if any(x in lname for x in TIER_N2): return "N2"
    if any(x in lname for x in TIER_N3): return "N3"
    
    # 4. Si pas trouvé, c'est une ligue inconnue (Gate-0)
    return "UNKNOWN"

def calculate_dcs(stats_home, stats_away, odds_data):
    """Calcule le score de confiance des données (0-100)"""
    score = 100
    
    # Pénalité si peu de matchs joués (saison débutante)
    try:
        h_played = stats_home.get('fixtures', {}).get('played', {}).get('total', 0)
        a_played = stats_away.get('fixtures', {}).get('played', {}).get('total', 0)
        if h_played < 5: score -= 20
        if a_played < 5: score -= 20
    except:
        score -= 30 # Données manquantes

    # Pénalité si pas de cotes fiables
    if not odds_data or not odds_data[0].get('bookmakers'):
        score -= 20
    
    return max(0, score)

def calculate_bookmaker_margin(odds_1x2):
    """Calcule la marge du bookmaker (overround)"""
    try:
        # odds_1x2 = [odd_home, odd_draw, odd_away]
        if not all(odds_1x2): return 1.0 # Invalide
        margin = (1/odds_1x2[0]) + (1/odds_1x2[1]) + (1/odds_1x2[2])
        return margin - 1.0
    except:
        return 1.0

# ====================== API HANDLER ======================
def safe_api_call(url):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code == 200: return resp.json()
        if resp.status_code == 429: print("🛑 QUOTA ATTEINT", flush=True)
    except Exception as e:
        print(f"⚠️ API Exception: {e}", flush=True)
    return None

def get_fixtures():
    today = time.strftime("%Y-%m-%d")
    return safe_api_call(f"{BASE_URL}/fixtures?date={today}").get('response', [])

def get_team_stats(tid, lid, season):
    return safe_api_call(f"{BASE_URL}/teams/statistics?team={tid}&league={lid}&season={season}").get('response')

def get_odds(fid):
    return safe_api_call(f"{BASE_URL}/odds?fixture={fid}").get('response', [])

# ====================== APEX ENGINE CORE ======================

def poisson_prob(l, k):
    try: return (math.exp(-l) * (l ** k)) / math.factorial(k)
    except: return 0

def run_monte_carlo(hxg, axg):
    probs = {"H": 0, "D": 0, "A": 0}
    hp = [poisson_prob(hxg, i) for i in range(7)]
    ap = [poisson_prob(axg, i) for i in range(7)]
    
    for h in range(7):
        for a in range(7):
            p = hp[h] * ap[a]
            # Dixon-Coles correction
            if h == 0 and a == 0: p *= (1 - RHO)
            elif h == 1 and a == 0: p *= (1 + RHO)
            elif h == 0 and a == 1: p *= (1 + RHO)
            elif h == 1 and a == 1: p *= (1 - RHO)
            
            if h > a: probs["H"] += p
            elif h == a: probs["D"] += p
            else: probs["A"] += p
    return probs

def calculate_value_bet(model_probs, odds_data, tier):
    values = []
    try:
        if not odds_data or not odds_data[0].get('bookmakers'): return None
        bm = odds_data[0]['bookmakers'][0] # Prendre le 1er bookmaker (Pinnacle si possible)
        
        # Extraction cotes 1X2
        odds_vals = {}
        for bet in bm['bets']:
            if bet['name'] == "Match Winner":
                for v in bet['values']:
                    odds_vals[v['value']] = float(v['odd'])
        
        if len(odds_vals) != 3: return None
        
        # Calcul Marge
        margin = calculate_bookmaker_margin([odds_vals.get('Home',0), odds_vals.get('Draw',0), odds_vals.get('Away',0)])
        if margin > MARGE_MAX_TIERS[tier]:
            return None # Marge trop élevée (cotes pièges)

        # Check Value
        edge_min = EDGE_MIN_TIERS[tier]
        
        for outcome, prob in model_probs.items():
            odd_key = {'H': 'Home', 'D': 'Draw', 'A': 'Away'}.get(outcome)
            odd = odds_vals.get(odd_key, 0)
            
            if odd < COTE_MIN or odd > COTE_MAX: continue
            
            implied = 1 / odd
            edge = prob - implied
            
            if edge > edge_min:
                icon = "🏠" if outcome == "H" else "⚖️" if outcome == "D" else "🏃"
                values.append(f"{icon} {outcome} VALUE: {prob*100:.1f}% vs {odd} (Edge +{edge*100:.1f}%)")
                
    except Exception as e:
        pass
    return "\n".join(values) if values else None

# ====================== NOTIFICATION ======================
def notify(msg, fid, league, dt, dcs, tier):
    if not bot: return
    key = f"{fid}"
    if key in sent_alerts: return
    sent_alerts.add(key)
    
    full = f"""🚨 APEX-ENGINE ALERT [{tier}]

🏆 {league}
🕒 {dt} (UTC)
📡 DCS: {dcs}/100

{msg}"""
    try:
        bot.send_message(CHAT_ID, full)
        value_bets_history.append({"time": datetime.now().strftime("%H:%M"), "message": full})
    except: pass

# ====================== CHECK ======================
def check_value_bets():
    if not API_KEY: return
    print(f"\n⏰ Check v1.2 à {datetime.now(timezone.utc).strftime('%H:%M:%S')}", flush=True)
    
    fixtures = get_fixtures()
    if not fixtures: return
    
    now = datetime.now(timezone.utc)
    count = 0
    
    for f in fixtures:
        # Filtre Temporel (Prochains 60 mins)
        try:
            m_date = datetime.fromisoformat(f['fixture']['date'].replace('Z', '+00:00'))
            if not (timedelta(minutes=0) < (m_date - now) < timedelta(minutes=60)):
                continue
        except: continue

        # Identification Ligue & Tier
        lname = f['league']['name']
        country = f['league']['country']
        tier = get_league_tier(lname, country)
        
        if tier in ["BLACKLIST", "UNKNOWN"]:
            continue
            
        # Limitation Quota (on analyse les P0/N1 en priorité)
        if count >= 15: break
        count += 1
        
        fid = f['fixture']['id']
        lid = f['league']['id']
        season = f['league']['season']
        ht = f['teams']['home']
        at = f['teams']['away']
        
        # Récupération données
        s_home = get_team_stats(ht['id'], lid, season)
        s_away = get_team_stats(at['id'], lid, season)
        
        if not s_home or not s_away: continue
        
        # Calcul DCS
        odds = get_odds(fid)
        dcs = calculate_dcs(s_home, s_away, odds)
        
        if dcs < DCS_MIN_TIERS[tier]:
            continue # Confiance insuffisante pour ce tier
            
        # Calcul Force & xG
        try:
            h_avg = s_home['goals']['for']['total']['total'] / s_home['fixtures']['played']['total']
            h_conc = s_home['goals']['against']['total']['total'] / s_home['fixtures']['played']['total']
            a_avg = s_away['goals']['for']['total']['total'] / s_away['fixtures']['played']['total']
            a_conc = s_away['goals']['against']['total']['total'] / s_away['fixtures']['played']['total']
            
            # Modèle simplifié pour stabilité
            h_atk = h_avg / LEAGUE_AVG_GOALS
            a_def = a_conc / LEAGUE_AVG_GOALS
            hxg = h_atk * a_def * LEAGUE_AVG_GOALS * HOME_ADVANTAGE
            
            a_atk = a_avg / LEAGUE_AVG_GOALS
            h_def = h_conc / LEAGUE_AVG_GOALS
            axg = a_atk * h_def * LEAGUE_AVG_GOALS
            
            # Simulation
            probs = run_monte_carlo(hxg, axg)
            
            # Value Bet Check
            val_msg = calculate_value_bet(probs, odds, tier)
            if val_msg:
                dt_str = f['fixture']['date'][:16].replace('T', ' ')
                msg = f"{ht['name']} vs {at['name']}\n\n{val_msg}"
                notify(msg, fid, lname, dt_str, dcs, tier)
            
        except:
            continue
            
        time.sleep(0.5)
    print(f"✅ Check terminé: {count} matchs analysés.", flush=True)

# ====================== SCHEDULER ======================
def run_scheduler():
    time.sleep(60)
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
