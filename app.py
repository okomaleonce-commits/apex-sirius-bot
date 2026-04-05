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

# ====================== FLASK APP ======================
app = Flask(__name__)

@app.route('/')
def home():
    return "🤖 APEX-ENGINE v1.3 - SHOOTING STYLE", 200

@app.route('/ping', methods=['GET', 'HEAD'])
def ping():
    return "pong", 200

@app.route('/test')
def test_route():
    threading.Thread(target=check_value_bets).start()
    return "✅ Scan manuel lancé.", 200

# ====================== CONFIG ======================
print("🚀 APEX-ENGINE v1.3 - SHOOTING NOTIFICATION STYLE", flush=True)

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
TIER_P0 = ["uefa champions league", "uefa europa league", "uefa europa conference league"]
TIER_N1 = ["premier league", "la liga", "bundesliga", "ligue 1", "serie a", "eredivisie", "liga portugal", "primeira liga", "scottish premiership", "jupiler pro league", "süper lig", "super lig", "eerste divisie", "rpl", "premier league russia", "super league greece"]
TIER_N2 = ["championship", "la liga 2", "laliga smartbank", "2. bundesliga", "ligue 2", "serie b", "liga portugal 2", "scottish championship", "challenger pro league", "tff 1. lig", "tweede divisie", "liga 1 romania", "österreichische bundesliga", "super league suisse", "superliga denmark", "allsvenskan", "eliteserien", "ekstraklasa", "czech first league", "otp bank liga", "superliga srbija", "hnl", "fortuna liga", "first professional league", "premier league ukraine", "israeli premier league"]
TIER_N3 = ["league one", "league two", "national league", "primera rfef", "3. liga", "national france", "serie c", "liga 3", "liga profesional argentina", "serie a brazil", "liga mx", "major league soccer", "j1 league", "k league 1", "saudi pro league", "arabian gulf league", "a-league", "premier soccer league", "ligue professionnelle 1", "mtn ligue 1"]

BLACKLIST_COUNTRIES = ["jordan", "indonesia", "vietnam", "kazakhstan", "azerbaijan", "georgia", "armenia", "belarus", "moldova", "kosovo", "malta", "cyprus", "faroe islands", "gibraltar", "san marino", "liechtenstein"]
BLACKLIST_KEYWORDS = ["u17", "u18", "u19", "u20", "u21", "u23", "ii", " b", "reserves", "youth", "primavera", "jong", "amateur", "development", "academy", "women", "womens"]

DCS_MIN_TIERS = { "P0": 65, "N1": 65, "N2": 70, "N3": 75 }
MARGE_MAX_TIERS = { "P0": 0.07, "N1": 0.09, "N2": 0.11, "N3": 0.12 }
EDGE_MIN_TIERS  = { "P0": 0.05, "N1": 0.05, "N2": 0.05, "N3": 0.06 }

COTE_MIN = 1.40
COTE_MAX = 8.00

# ====================== HELPERS ======================
def get_league_tier(league_name, country):
    lname = league_name.lower()
    for kw in BLACKLIST_KEYWORDS:
        if kw in lname: return "BLACKLIST"
    if country.lower() in BLACKLIST_COUNTRIES: return "BLACKLIST"
    if any(x in lname for x in TIER_P0): return "P0"
    if any(x in lname for x in TIER_N1): return "N1"
    if any(x in lname for x in TIER_N2): return "N2"
    if any(x in lname for x in TIER_N3): return "N3"
    return "UNKNOWN"

def calculate_dcs(stats_home, stats_away, odds_data):
    score = 100
    try:
        if stats_home.get('fixtures', {}).get('played', {}).get('total', 0) < 5: score -= 20
        if stats_away.get('fixtures', {}).get('played', {}).get('total', 0) < 5: score -= 20
    except: score -= 30
    if not odds_data or not odds_data[0].get('bookmakers'): score -= 20
    return max(0, score)

def calculate_bookmaker_margin(odds_1x2):
    try:
        if not all(odds_1x2): return 1.0
        return sum([1/o for o in odds_1x2]) - 1.0
    except: return 1.0

# ====================== API ======================
def safe_api_call(url):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code == 200: return resp.json()
        if resp.status_code == 429: print("🛑 QUOTA ATTEINT", flush=True)
    except: pass
    return None

def get_fixtures():
    return safe_api_call(f"{BASE_URL}/fixtures?date={time.strftime('%Y-%m-%d')}").get('response', [])

def get_team_stats(tid, lid, season):
    return safe_api_call(f"{BASE_URL}/teams/statistics?team={tid}&league={lid}&season={season}").get('response')

def get_odds(fid):
    return safe_api_call(f"{BASE_URL}/odds?fixture={fid}").get('response', [])

# ====================== ENGINE ======================
def poisson_prob(l, k):
    try: return (math.exp(-l) * (l ** k)) / math.factorial(k)
    except: return 0

def run_monte_carlo(hxg, axg):
    probs = {"H": 0, "D": 0, "A": 0, "O25": 0}
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
            if h+a >= 3: probs["O25"] += p
    return probs

def analyze_markets(model_probs, odds_data, tier):
    """Retourne une liste de paris value trouvés (structurée)"""
    opportunities = []
    if not odds_data or not odds_data[0].get('bookmakers'): return []
    
    bm = odds_data[0]['bookmakers'][0] # Focus Pinnacle ou 1er sharp
    
    # Extraction des cotes
    odds_1x2 = {}
    odds_ou = {}
    
    for bet in bm['bets']:
        if bet['name'] == "Match Winner":
            for v in bet['values']: odds_1x2[v['value']] = float(v['odd'])
        if bet['name'] == "Goals Over/Under":
            for v in bet['values']:
                if "Over 2.5" in v['value']: odds_ou['Over 2.5'] = float(v['odd'])
                if "Under 2.5" in v['value']: odds_ou['Under 2.5'] = float(v['odd'])

    # Check Margin
    if odds_1x2:
        margin = calculate_bookmaker_margin([odds_1x2.get('Home',0), odds_1x2.get('Draw',0), odds_1x2.get('Away',0)])
        if margin > MARGE_MAX_TIERS[tier]: return [] # Marge trop haute

    # Analyse 1X2
    edge_min = EDGE_MIN_TIERS[tier]
    
    mapping = {'Home': ('H', 'HOME WIN'), 'Draw': ('D', 'DRAW'), 'Away': ('A', 'AWAY WIN')}
    
    for market_name, (proba_key, label) in mapping.items():
        odd = odds_1x2.get(market_name, 0)
        if odd < COTE_MIN or odd > COTE_MAX: continue
        
        implied = 1 / odd
        edge = model_probs[proba_key] - implied
        
        if edge > edge_min:
            opportunities.append({
                "type": "1X2",
                "label": label, # Sera remplacé par nom équipe plus tard
                "odd": odd,
                "edge": edge
            })
            
    # Analyse Over 2.5
    if 'Over 2.5' in odds_ou:
        odd = odds_ou['Over 2.5']
        if odd >= COTE_MIN:
            implied = 1/odd
            edge = model_probs['O25'] - implied
            if edge > edge_min:
                opportunities.append({
                    "type": "OU",
                    "label": "Over 2.5",
                    "odd": odd,
                    "edge": edge
                })
                
    return opportunities

# ====================== NOTIFICATION STYLE ======================
def envoyer_notification(opps, fixture_info, dcs, tier):
    if not bot: return
    fid = fixture_info['id']
    key = f"{fid}"
    if key in sent_alerts: return
    sent_alerts.add(key)

    # Tri par Edge décroissant
    opps.sort(key=lambda x: x['edge'], reverse=True)
    main = opps[0]
    alts = opps[1:]

    # Formatage Nom Sélection
    if main['type'] == "1X2":
        if main['label'] == "HOME WIN": selection_name = fixture_info['home']
        elif main['label'] == "AWAY WIN": selection_name = fixture_info['away']
        else: selection_name = "Draw"
    else:
        selection_name = main['label']

    # Construction du message
    msg = f"""⚽ SOCCER ⚽

Selection: {selection_name}

Min. Odds: 🚀{main['odd']:.2f}🚀

{fixture_info['home']} vs {fixture_info['away']}
{fixture_info['country']} - {fixture_info['league']}
{fixture_info['date']} (UTC)

📡 APEX Signal: DCS {dcs}/100 | Edge +{main['edge']*100:.1f}%
"""

    # Ajout Alternatives
    if alts:
        msg += "\nAlternatives:\n"
        for alt in alts:
            # Format court pour alternatives
            alt_name = alt['label']
            if alt['type'] == "1X2":
                 if alt['label'] == "HOME WIN": alt_name = fixture_info['home']
                 elif alt['label'] == "AWAY WIN": alt_name = fixture_info['away']
                 else: alt_name = "Draw"
            
            msg += f"▪ {alt_name} --> {alt['odd']:.2f}\n"

    try:
        bot.send_message(CHAT_ID, msg)
        value_bets_history.append({"time": datetime.now().strftime("%H:%M"), "message": msg})
        print(f"✅ Telegram envoyé pour {fid}", flush=True)
    except Exception as e:
        print(f"❌ Erreur Telegram: {e}", flush=True)

# ====================== CHECK ======================
def check_value_bets():
    if not API_KEY: return
    print(f"\n⏰ Check v1.3 à {datetime.now(timezone.utc).strftime('%H:%M:%S')}", flush=True)
    
    fixtures = get_fixtures()
    if not fixtures: return
    
    now = datetime.now(timezone.utc)
    count = 0
    
    for f in fixtures:
        # Temporal Filter
        try:
            m_date = datetime.fromisoformat(f['fixture']['date'].replace('Z', '+00:00'))
            if not (timedelta(minutes=0) < (m_date - now) < timedelta(minutes=60)): continue
        except: continue

        # Tier Filter
        lname = f['league']['name']
        country = f['league']['country']
        tier = get_league_tier(lname, country)
        if tier in ["BLACKLIST", "UNKNOWN"]: continue
        
        # Limit
        if count >= 15: break
        count += 1
        
        # Data Fetch
        fid = f['fixture']['id']
        lid = f['league']['id']
        season = f['league']['season']
        ht = f['teams']['home']
        at = f['teams']['away']
        
        s_home = get_team_stats(ht['id'], lid, season)
        s_away = get_team_stats(at['id'], lid, season)
        if not s_home or not s_away: continue
        
        odds = get_odds(fid)
        dcs = calculate_dcs(s_home, s_away, odds)
        
        if dcs < DCS_MIN_TIERS[tier]: continue
        
        # Math Engine
        try:
            h_avg = s_home['goals']['for']['total']['total'] / s_home['fixtures']['played']['total']
            h_conc = s_home['goals']['against']['total']['total'] / s_home['fixtures']['played']['total']
            a_avg = s_away['goals']['for']['total']['total'] / s_away['fixtures']['played']['total']
            a_conc = s_away['goals']['against']['total']['total'] / s_away['fixtures']['played']['total']
            
            hxg = (h_avg / LEAGUE_AVG_GOALS) * (a_conc / LEAGUE_AVG_GOALS) * LEAGUE_AVG_GOALS * HOME_ADVANTAGE
            axg = (a_avg / LEAGUE_AVG_GOALS) * (h_conc / LEAGUE_AVG_GOALS) * LEAGUE_AVG_GOALS
            
            probs = run_monte_carlo(hxg, axg)
            
            # Recherche Value
            opportunities = analyze_markets(probs, odds, tier)
            
            if opportunities:
                info = {
                    'id': fid, 'league': lname, 'country': country,
                    'home': ht['name'], 'away': at['name'],
                    'date': f['fixture']['date'][:16].replace('T', ' ')
                }
                envoyer_notification(opportunities, info, dcs, tier)
            
        except: continue
        time.sleep(0.5)
        
    print(f"✅ Check terminé: {count} analysés.", flush=True)

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
