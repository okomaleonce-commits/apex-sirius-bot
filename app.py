import requests
import telebot
import time
import os
import sys
import threading
import math
from flask import Flask
from datetime import datetime, timezone, timedelta

# ====================== FLASK APP ======================
app = Flask(__name__)

@app.route('/')
def home():
    return "🤖 APEX-ENGINE v2.2 - ROBUST", 200

@app.route('/ping', methods=['GET', 'HEAD'])
def ping():
    return "pong", 200

# ====================== CONFIG ======================
print("🚀 APEX-ENGINE v2.2 - RESTART", flush=True)

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
COTE_MIN = 1.40
COTE_MAX = 8.00

# Whitelists (raccourcies pour lisibilité, à compléter si besoin)
TIER_P0 = ["uefa champions league", "uefa europa league", "uefa europa conference league"]
TIER_N1 = ["premier league", "championship", "la liga", "bundesliga", "ligue 1", "serie a", "eredivisie", "liga portugal", "primeira liga", "jupiler pro league"]
TIER_N2 = ["süper lig", "super lig", "russian premier league", "super league 1", "bundesliga autrichienne", "super league suisse", "superliga", "allsvenskan", "eliteserien", "ekstraklasa", "czech first league", "otp bank liga", "liga 1", "hnl"]
TIER_N3 = ["major league soccer", "liga mx", "liga profesional argentina", "brasileirão série a", "j1 league", "k league 1", "saudi pro league"]
TIER_N4 = ["botola pro", "egyptian premier league", "tunisian ligue professionnelle 1"]

BLACKLIST_KEYWORDS = ["u17", "u18", "u19", "u20", "u21", "u23", "ii", " b ", "reserves", "youth", "women", "womens", "femenil"]

DCS_MIN_TIERS = { "P0": 65, "N1": 65, "N2": 70, "N3": 75, "N4": 78 }
EDGE_MIN_TIERS  = { "P0": 0.05, "N1": 0.05, "N2": 0.05, "N3": 0.06, "N4": 0.07 }

# ====================== FOOTYSTATS BRIDGE ======================
def get_fs_team_id(team_name):
    if team_name in fs_teams_cache:
        return fs_teams_cache[team_name]
    try:
        url = f"{FS_URL}/search?key={FOOTYSTATS_KEY}&search_term={team_name}"
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            for item in data.get('data', []):
                if item.get('type') == 'team' and item.get('name', '').lower() == team_name.lower():
                    tid = item.get('id')
                    fs_teams_cache[team_name] = tid
                    return tid
    except: pass
    return None

def get_footystats_xg(team_id):
    if not team_id: return None, None
    try:
        url = f"{FS_URL}/team?key={FOOTYSTATS_KEY}&team_id={team_id}"
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            stats = data.get('data', {}).get('xG', {})
            xg_for = stats.get('total_xG')
            xg_ag = stats.get('total_xGA')
            played = data.get('data', {}).get('matches_played', 1)
            if played > 0 and xg_for is not None:
                return xg_for / played, xg_ag / played
    except: pass
    return None, None

# ====================== HELPERS ======================
def get_league_tier(league_name, country):
    lname = league_name.lower()
    for kw in BLACKLIST_KEYWORDS:
        if kw in lname: return "BLACKLIST"
    if any(x in lname for x in TIER_P0): return "P0"
    if any(x in lname for x in TIER_N1): return "N1"
    if any(x in lname for x in TIER_N2): return "N2"
    if any(x in lname for x in TIER_N3): return "N3"
    if any(x in lname for x in TIER_N4): return "N4"
    return "UNKNOWN"

# ====================== API HANDLERS ======================
def safe_api_call(url):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code == 200: return resp.json()
    except: pass
    return None

def get_fixtures():
    return safe_api_call(f"{BASE_URL}/fixtures?date={time.strftime('%Y-%m-%d')}").get('response', [])

def get_team_stats(tid, lid, season):
    return safe_api_call(f"{BASE_URL}/teams/statistics?team={tid}&league={lid}&season={season}").get('response')

def get_odds(fid):
    return safe_api_call(f"{BASE_URL}/odds?fixture={fid}").get('response', [])

# ====================== ENGINE ======================
def calculate_smart_xg(stats):
    try:
        played = stats.get('fixtures', {}).get('played', {}).get('total', 1)
        if played == 0: played = 1
        goals = stats.get('goals', {}).get('for', {}).get('total', {}).get('total', 0)
        return goals / played
    except: return 1.3

def calculate_strength_model(stats_home, stats_away, fs_data=None):
    # 1. FootyStats Priorité
    if fs_data:
        hxg = fs_data.get('home_xg')
        axg = fs_data.get('away_xg')
        if hxg and axg:
            return hxg * 1.10, axg

    # 2. Fallback API
    home_attack = calculate_smart_xg(stats_home)
    away_attack = calculate_smart_xg(stats_away)
    
    # Simplifié pour robustesse
    hxg = home_attack * 1.1
    axg = away_attack
    
    return hxg, axg

def run_monte_carlo(hxg, axg):
    probs = {"H": 0, "D": 0, "A": 0, "O25": 0}
    # Calcul matriciel simple (Poisson approx)
    # Juste une approx rapide pour éviter le crash si hxg est étrange
    try:
        # Home prob ~ hxg / (hxg + axg)
        total = hxg + axg
        if total == 0: return probs
        
        probs['H'] = (hxg / total) * 0.9 # Adjustement empirique
        probs['A'] = (axg / total) * 0.9
        probs['D'] = 1.0 - probs['H'] - probs['A']
        probs['O25'] = min(1.0, (hxg + axg) / 3.0) # Approx linéaire
    except: pass
    return probs

# ====================== MARKET ANALYZER ======================
def analyze_markets(model_probs, odds_data, tier):
    opportunities = []
    if not odds_data: return []
    
    best_odds = {"Home": 0, "Draw": 0, "Away": 0}
    best_bookie = {}

    for bm_data in odds_data:
        for bm in bm_data.get('bookmakers', []):
            bm_name = bm['name']
            for bet in bm['bets']:
                if bet['name'] == "Match Winner":
                    for v in bet['values']:
                        odd = float(v['odd'])
                        key = v['value']
                        if odd > best_odds.get(key, 0):
                            best_odds[key] = odd
                            best_bookie[key] = bm_name

    edge_min = EDGE_MIN_TIERS[tier]
    
    for key, prob_key in [("Home", "H"), ("Draw", "D"), ("Away", "A")]:
        odd = best_odds[key]
        if odd >= COTE_MIN and odd <= COTE_MAX:
            implied = 1/odd
            edge = model_probs[prob_key] - implied
            if edge > edge_min:
                opportunities.append({
                    "type": "1X2", "label": key.upper(), "odd": odd, 
                    "edge": edge, "proba_key": prob_key, "is_value": True, 
                    "bookie": best_bookie.get(key, "N/A")
                })
    return opportunities

# ====================== NOTIFICATION ======================
def envoyer_notification(opps, fixture_info, tier, hxg, axg, source="API"):
    if not bot: return
    fid = fixture_info['id']
    if fid in sent_alerts: return
    sent_alerts.add(fid)

    main = opps[0]
    sel_name = main['label']
    if main['type'] == "1X2":
        if main['label'] == "HOME WIN": sel_name = fixture_info['home']
        elif main['label'] == "AWAY WIN": sel_name = fixture_info['away']
        else: sel_name = "Draw"

    msg = f"⚽ {fixture_info['home']} vs {fixture_info['away']}\n"
    msg += f"🌍 {fixture_info['league']}\n\n"
    msg += f"📊 xG ({source}): {hxg:.2f} - {axg:.2f}\n"
    msg += f"🚨 VALUE BET\nSelection: {sel_name}\nOdds: 🚀{main['odd']:.2f}🚀\nEdge: +{main['edge']*100:.1f}%"

    try:
        bot.send_message(CHAT_ID, msg)
        print(f"✅ TELEGRAM SENT FOR {fid}", flush=True)
        # Track
        tracked_bets.append({
            "fid": fid, "home": fixture_info['home'], "away": fixture_info['away'],
            "league": fixture_info['league'], "date": fixture_info['date'],
            "signal_name": sel_name, "prediction_type": main['proba_key'], "odd": main['odd']
        })
    except Exception as e:
        print(f"❌ Telegram Error: {e}", flush=True)

# ====================== CHECK MAIN ======================
def check_value_bets():
    if not API_KEY: return
    print(f"\n⏰ Check v2.2 at {datetime.now(timezone.utc).strftime('%H:%M:%S')}", flush=True)

    fixtures = get_fixtures()
    if not fixtures: return
    
    now = datetime.now(timezone.utc)
    count = 0
    
    for f in fixtures:
        try:
            m_date = datetime.fromisoformat(f['fixture']['date'].replace('Z', '+00:00'))
            if not (timedelta(minutes=0) < (m_date - now) < timedelta(minutes=60)): continue
        except: continue

        lname = f['league']['name']
        tier = get_league_tier(lname, f['league']['country'])
        if tier in ["BLACKLIST", "UNKNOWN"]: continue
        
        if count >= 10: break
        count += 1
        
        fid = f['fixture']['id']
        
        s_home = get_team_stats(f['teams']['home']['id'], f['league']['id'], f['league']['season'])
        s_away = get_team_stats(f['teams']['away']['id'], f['league']['id'], f['league']['season'])
        if not s_home or not s_away: continue
        
        # Correct Dictionary Init
        fs_data = {
            'home_xg': None,
            'home_xga': None,
            'away_xg': None,
            'away_xga': None
        }
        data_source = "API"
        
        if FOOTYSTATS_KEY:
            tid_home = get_fs_team_id(f['teams']['home']['name'])
            tid_away = get_fs_team_id(f['teams']['away']['name'])
            if tid_home and tid_away:
                xg_h, xga_h = get_footystats_xg(tid_home)
                xg_a, xga_a = get_footystats_xg(tid_away)
                if xg_h and xga_a:
                    fs_data['home_xg'] = xg_h
                    fs_data['home_xga'] = xga_h
                    fs_data['away_xg'] = xg_a
                    fs_data['away_xga'] = xga_a
                    data_source = "FootyStats"
        
        odds = get_odds(fid)
        hxg, axg = calculate_strength_model(s_home, s_away, fs_data)
        
        # Logs debug
        print(f"DEBUG: {f['teams']['home']['name']} HXG={hxg:.2f} AXG={axg:.2f} Source={data_source}", flush=True)

        if hxg and axg:
            probs = run_monte_carlo(hxg, axg)
            opportunities = analyze_markets(probs, odds, tier)
            
            if opportunities:
                info = {
                    'id': fid, 'league': lname, 'country': f['league']['country'],
                    'home': f['teams']['home']['name'], 'away': f['teams']['away']['name'],
                    'date': f['fixture']['date'][:16].replace('T', ' ')
                }
                envoyer_notification(opportunities, info, tier, hxg, axg, data_source)
        
        time.sleep(0.5)
        
    print(f"✅ Check done: {count} analyzed.", flush=True)

# ====================== SCHEDULER (FIXED) ======================
def run_scheduler():
    print("🗓️ Worker thread started.", flush=True)
    while True:
        try:
            check_value_bets()
        except Exception as e:
            print(f"❌ Loop error: {e}", flush=True)
        
        # Sleep 15 minutes
        time.sleep(900)

# Start Thread
if bot:
    threading.Thread(target=run_scheduler, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
