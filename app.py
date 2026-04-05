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
    return "🤖 APEX-ENGINE v1.6 - 150 LEAGUES WHITELIST", 200

@app.route('/ping', methods=['GET', 'HEAD'])
def ping():
    return "pong", 200

@app.route('/test')
def test_route():
    threading.Thread(target=check_value_bets).start()
    return "✅ Scan manuel lancé.", 200

# ====================== CONFIG ======================
print("🚀 APEX-ENGINE v1.6 - EXTENDED CONFIG LOADING", flush=True)

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
tracked_bets = []

# ====================== A-LAP CONFIGURATION ======================
LEAGUE_AVG_GOALS = 2.65
HOME_ADVANTAGE = 1.10
RHO = 0.10

# --- WHITELIST 150 LIGUES (V1.6) ---

# NIVEAU P0 — UEFA (3 Ligues)
TIER_P0 = [
    "uefa champions league", "uefa europa league", "uefa europa conference league"
]

# NIVEAU 1 — TOP EUROPE (22 Ligues)
TIER_N1 = [
    "premier league", "championship", "league one", "league two",
    "la liga", "la liga 2", "laliga smartbank",
    "bundesliga", "2. bundesliga", "3. liga",
    "ligue 1", "ligue 2",
    "serie a", "serie b",
    "liga portugal", "primeira liga", "liga portugal 2",
    "eredivisie", "eerste divisie",
    "jupiler pro league", "challenger pro league",
    "premiership", "scottish championship", "scottish league one"
]

# NIVEAU 2 — EUROPE SOLIDE (47 Ligues)
TIER_N2 = [
    "süper lig", "super lig", "tff 1. lig",
    "russian premier league", "fnl",
    "ukrainian premier league", "persha liha",
    "super league 1", "super league 2", "super league greece",
    "bundesliga autrichienne", "2. liga autrichienne",
    "super league suisse", "challenge league suisse",
    "superliga", "1. division", "denmark superliga",
    "allsvenskan", "superettan",
    "eliteserien", "1. divisjon",
    "veikkausliiga",
    "ekstraklasa", "i liga",
    "czech first league", "czech national football league",
    "fortuna liga",
    "otp bank liga", "nemzeti bajnokság ii",
    "liga 1", "liga 2", "liga 1 romania",
    "superliga srbija", "prva liga",
    "hnl", "1. nl",
    "prva liga telekom", "prva liga slovenije",
    "premier league de bosnie",
    "first professional league", "second professional league",
    "kategoria superiore",
    "prva makedonska",
    "meridianbet", "1. cfl",
    "1re division chypriote", "cyprus division",
    "israeli premier league", "liga leumit",
    "league of ireland", "premier division",
    "nifl premiership",
    "cymru premier",
    "kazakhstan premier league",
    "azerbaijan premier league"
]

# NIVEAU 3 — AMÉRIQUES & ASIE (35 Ligues)
TIER_N3 = [
    "major league soccer", "usl championship",
    "liga mx", "liga de expansión mx",
    "liga profesional argentina", "primera nacional",
    "brasileirão série a", "brasileirão série b", "serie a brazil", "serie b brazil",
    "chilean primera división",
    "colombian primera a",
    "liga 1 perú",
    "campeonato uruguayo",
    "ligapro ecuador",
    "copa libertadores", "copa sudamericana",
    "j1 league", "j2 league",
    "k league 1", "k league 2",
    "chinese super league", "china super league", "chinese league one",
    "indian super league",
    "saudi pro league", "roshn saudi league",
    "uae arabian gulf league",
    "qatar stars league",
    "persian gulf pro league",
    "thai league 1",
    "malaysian super league",
    "singapore premier league",
    "v.league 1",
    "a-league", "a-league men",
    "nrfl",
    "afc champions league", "afc champions league elite", "afc cup"
]

# NIVEAU 4 — AFRIQUE & MOYEN-ORIENT (28 Ligues)
TIER_N4 = [
    "botola pro",
    "caf champions league", "caf confederation cup",
    "egyptian premier league",
    "tunisian ligue professionnelle 1",
    "algerian ligue professionnelle 1",
    "premier soccer league", "psl",
    "libyan premier league",
    "nigerian premier football league",
    "kenyan premier league",
    "tanzanian premier league",
    "ugandan super league",
    "zambia super league",
    "zimbabwean premier soccer league",
    "cameroon elite one",
    "senegalese ligue 1",
    "mtn ligue 1", "côte d'ivoire",
    "ghanaian premier league",
    "jordan pro league",
    "lebanese premier league",
    "iraqi premier league",
    "bahraini premier league",
    "omani professional league",
    "kuwaiti premier league",
    "lithuanian a lyga",
    "latvian higher league",
    "estonian meistriliiga",
    "belarusian premier league",
    "moldovan national division",
    "georgian erovnuli liga",
    "armenian premier league"
    # Note: N5 (Surveillance) exclu de la Whitelist pour auto-alertes
]

# --- BLACKLIST ---
BLACKLIST_KEYWORDS = [
    "u17", "u18", "u19", "u20", "u21", "u23", "u20", "u23",
    "ii", " b team", " b ", "reserves", "youth", "primavera", "jong",
    "amateur", "development", "academy", "filial", "reserve",
    "juniores", "sub-", "women", "womens"
]

# --- SEUILS PAR NIVEAU (CONFIG TABLEAU) ---
DCS_MIN_TIERS = {
    "P0": 65, "N1": 65, "N2": 70, "N3": 75, "N4": 78
}

MARGE_MAX_TIERS = {
    "P0": 0.07, "N1": 0.09, "N2": 0.11, "N3": 0.12, "N4": 0.13
}

EDGE_MIN_TIERS = {
    "P0": 0.05, "N1": 0.05, "N2": 0.05, "N3": 0.06, "N4": 0.07
}

COTE_MIN = 1.40
COTE_MAX = 8.00

# ====================== HELPERS ======================
def get_league_tier(league_name, country):
    lname = league_name.lower()
    
    # 1. Blacklist Mots Clés
    for kw in BLACKLIST_KEYWORDS:
        if kw in lname:
            return "BLACKLIST"
            
    # 2. Check Whitelist (Ordre important: P0 -> N1 -> ... -> N4)
    if any(x in lname for x in TIER_P0): return "P0"
    if any(x in lname for x in TIER_N1): return "N1"
    if any(x in lname for x in TIER_N2): return "N2"
    if any(x in lname for x in TIER_N3): return "N3"
    if any(x in lname for x in TIER_N4): return "N4"
    
    # 3. Si pas dans la whitelist -> Gate-0 (Inconnu)
    return "UNKNOWN"

def calculate_dcs(stats_home, stats_away, odds_data):
    score = 100
    try:
        h_played = stats_home.get('fixtures', {}).get('played', {}).get('total', 0)
        a_played = stats_away.get('fixtures', {}).get('played', {}).get('total', 0)
        if h_played < 5: score -= 20
        if a_played < 5: score -= 20
        # Pénalité supplémentaire si données xG manquantes (rare mais possible)
        if not stats_home.get('goals', {}).get('for', {}).get('total', {}).get('total'):
            score -= 15
    except: 
        score -= 30
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
    opportunities = []
    if not odds_data or not odds_data[0].get('bookmakers'): return []
    
    bm = odds_data[0]['bookmakers'][0]
    odds_1x2 = {}
    odds_ou = {}
    
    for bet in bm['bets']:
        if bet['name'] == "Match Winner":
            for v in bet['values']: odds_1x2[v['value']] = float(v['odd'])
        if bet['name'] == "Goals Over/Under":
            for v in bet['values']:
                if "Over 2.5" in v['value']: odds_ou['Over 2.5'] = float(v['odd'])

    if odds_1x2:
        margin = calculate_bookmaker_margin([odds_1x2.get('Home',0), odds_1x2.get('Draw',0), odds_1x2.get('Away',0)])
        if margin > MARGE_MAX_TIERS[tier]: return []

    edge_min = EDGE_MIN_TIERS[tier]
    
    # Check Home
    if 'Home' in odds_1x2:
        odd = odds_1x2['Home']
        if COTE_MIN <= odd <= COTE_MAX:
            implied = 1 / odd
            edge = model_probs['H'] - implied
            is_val = edge > edge_min
            opportunities.append({"type": "1X2", "label": "HOME WIN", "odd": odd, "edge": edge, "proba_key": "H", "is_value": is_val})

    # Check Draw
    if 'Draw' in odds_1x2:
        odd = odds_1x2['Draw']
        if COTE_MIN <= odd <= COTE_MAX:
            implied = 1 / odd
            edge = model_probs['D'] - implied
            is_val = edge > edge_min
            opportunities.append({"type": "1X2", "label": "DRAW", "odd": odd, "edge": edge, "proba_key": "D", "is_value": is_val})
            
    # Check Away
    if 'Away' in odds_1x2:
        odd = odds_1x2['Away']
        if COTE_MIN <= odd <= COTE_MAX:
            implied = 1 / odd
            edge = model_probs['A'] - implied
            is_val = edge > edge_min
            opportunities.append({"type": "1X2", "label": "AWAY WIN", "odd": odd, "edge": edge, "proba_key": "A", "is_value": is_val})
            
    # Check Over 2.5
    if 'Over 2.5' in odds_ou:
        odd = odds_ou['Over 2.5']
        if odd >= COTE_MIN:
            implied = 1/odd
            edge = model_probs['O25'] - implied
            is_val = edge > edge_min
            opportunities.append({"type": "OU", "label": "Over 2.5", "odd": odd, "edge": edge, "proba_key": "O25", "is_value": is_val})
                
    return opportunities

def get_strong_signal(home_xg, away_xg, probs):
    xg_diff = home_xg - away_xg
    total_xg = home_xg + away_xg
    
    if xg_diff > 1.2: return f"🛡️ SIGNAL: DOMINANCE DOMICILE (xG {home_xg:.2f} vs {away_xg:.2f})"
    elif xg_diff < -1.2: return f"🛡️ SIGNAL: DOMINANCE EXTERIEURE (xG {away_xg:.2f} vs {home_xg:.2f})"
    if total_xg > 3.5: return f"🔥 SIGNAL: MATCH OUVERT (xG Total {total_xg:.2f})"
    if total_xg < 2.0: return f"🧱 SIGNAL: MATCH FERME (xG Total {total_xg:.2f})"
    
    max_prob = max(probs['H'], probs['D'], probs['A'])
    if max_prob > 0.55:
        if probs['H'] == max_prob: return f"📈 SIGNAL: VICTOIRE DOMICILE LIKELY ({probs['H']*100:.0f}%)"
        if probs['A'] == max_prob: return f"📈 SIGNAL: VICTOIRE EXTERIEURE LIKELY ({probs['A']*100:.0f}%)"
    return "⚖️ SIGNAL: EQUILIBRE TACTIQUE"

# ====================== RESULT TRACKER ======================
def check_results(fixtures_today):
    global tracked_bets
    if not tracked_bets: return

    finished_bets = []
    still_pending = []
    report_lines = []
    
    for bet in tracked_bets:
        match_data = next((m for m in fixtures_today if m['fixture']['id'] == bet['fid']), None)
        
        if match_data:
            status = match_data['fixture']['status']['short']
            
            if status in ['FT', 'AET', 'PEN']:
                score_home = match_data['goals']['home']
                score_away = match_data['goals']['away']
                total_goals = score_home + score_away
                
                is_win = False
                p_type = bet['prediction_type']
                
                if p_type == "H" and score_home > score_away: is_win = True
                elif p_type == "D" and score_home == score_away: is_win = True
                elif p_type == "A" and score_home < score_away: is_win = True
                elif p_type == "O25" and total_goals >= 3: is_win = True
                
                result_icon = "✅ GAGNE" if is_win else "❌ PERDU"
                
                line = f"""📅 {bet['date']}
🏆 {bet['league']}
⚽ {bet['home']} vs {bet['away']}

🔮 Signal: {bet['signal_name']} @ {bet['odd']:.2f}
🏁 Score Final: {score_home} - {score_away}
{result_icon}
------------------------"""
                report_lines.append(line)
                finished_bets.append(bet)
            
            elif status in ['CANC', 'PST', 'ABD']:
                finished_bets.append(bet)
            else:
                still_pending.append(bet)
        else:
            still_pending.append(bet)

    tracked_bets = still_pending
    
    if report_lines:
        header = "📊 RÉSULTATS DES PRÉCÉDENTS PARIS 📊\n\n"
        try:
            if bot: bot.send_message(CHAT_ID, header + "\n".join(report_lines))
        except: pass

# ====================== NOTIFICATION ======================
def envoyer_notification(opps, fixture_info, dcs, tier, strong_signal, hxg, axg):
    if not bot: return
    fid = fixture_info['id']
    key = f"{fid}"
    if key in sent_alerts: return
    sent_alerts.add(key)

    value_bets = [o for o in opps if o['is_value']]
    info_bets = [o for o in opps if not o['is_value']]

    msg = f"""⚽ SOCCER ⚽

{fixture_info['home']} vs {fixture_info['away']}
🌍 {fixture_info['country']} - {fixture_info['league']}
🕒 {fixture_info['date']} (UTC)

{strong_signal}

📊 APEX Model (xG: {hxg:.2f} - {axg:.2f})
"""

    if value_bets:
        main = value_bets[0]
        selection_name = ""
        if main['type'] == "1X2":
            if main['label'] == "HOME WIN": selection_name = fixture_info['home']
            elif main['label'] == "AWAY WIN": selection_name = fixture_info['away']
            else: selection_name = "Draw"
        else:
            selection_name = main['label']

        msg += f"""
🚨 VALUE BET DETECTED 🚨
Selection: {selection_name}
Min. Odds: 🚀{main['odd']:.2f}🚀
Edge: +{main['edge']*100:.1f}%
"""
        tracked_bets.append({
            "fid": fid, "home": fixture_info['home'], "away": fixture_info['away'],
            "league": fixture_info['league'], "date": fixture_info['date'],
            "signal_name": selection_name, "prediction_type": main['proba_key'], "odd": main['odd']
        })
    else:
        msg += "\n📉 VALUE BET: Aucune value significative détectée.\n"

    if info_bets:
        msg += "\n💡 Autres marchés probables:\n"
        for o in info_bets[:2]:
            name = o['label']
            if o['type'] == "1X2":
                 if o['label'] == "HOME WIN": name = fixture_info['home']
                 elif o['label'] == "AWAY WIN": name = fixture_info['away']
                 else: name = "Draw"
            prob_pct = o['edge'] + (1/o['odd'])
            msg += f"▪ {name} @ {o['odd']:.2f} (Proba {prob_pct*100:.0f}%)\n"

    try:
        bot.send_message(CHAT_ID, msg)
        value_bets_history.append({"time": datetime.now().strftime("%H:%M"), "message": msg})
        print(f"✅ Telegram envoyé pour {fid}", flush=True)
    except Exception as e:
        print(f"❌ Erreur Telegram: {e}", flush=True)

# ====================== CHECK ======================
def check_value_bets():
    if not API_KEY: return
    print(f"\n⏰ Check v1.6 à {datetime.now(timezone.utc).strftime('%H:%M:%S')}", flush=True)
    
    fixtures = get_fixtures()
    if not fixtures: return
    
    check_results(fixtures)
    
    now = datetime.now(timezone.utc)
    count = 0
    
    for f in fixtures:
        try:
            m_date = datetime.fromisoformat(f['fixture']['date'].replace('Z', '+00:00'))
            if not (timedelta(minutes=0) < (m_date - now) < timedelta(minutes=60)): continue
        except: continue

        lname = f['league']['name']
        country = f['league']['country']
        tier = get_league_tier(lname, country)
        
        # IMPORTANT: On ignore UNKNOWN et BLACKLIST (ce qui inclut N5 manuel)
        if tier in ["BLACKLIST", "UNKNOWN"]: continue
        
        if count >= 20: break # Limité à 20 pour le quota API
        count += 1
        
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
        
        # Vérification DCS par Tier
        if dcs < DCS_MIN_TIERS[tier]: continue
        
        try:
            h_avg = s_home['goals']['for']['total']['total'] / s_home['fixtures']['played']['total']
            h_conc = s_home['goals']['against']['total']['total'] / s_home['fixtures']['played']['total']
            a_avg = s_away['goals']['for']['total']['total'] / s_away['fixtures']['played']['total']
            a_conc = s_away['goals']['against']['total']['total'] / s_away['fixtures']['played']['total']
            
            hxg = (h_avg / LEAGUE_AVG_GOALS) * (a_conc / LEAGUE_AVG_GOALS) * LEAGUE_AVG_GOALS * HOME_ADVANTAGE
            axg = (a_avg / LEAGUE_AVG_GOALS) * (h_conc / LEAGUE_AVG_GOALS) * LEAGUE_AVG_GOALS
            
            probs = run_monte_carlo(hxg, axg)
            opportunities = analyze_markets(probs, odds, tier)
            signal = get_strong_signal(hxg, axg, probs)
            
            if any(o['is_value'] for o in opportunities):
                info = {
                    'id': fid, 'league': lname, 'country': country,
                    'home': ht['name'], 'away': at['name'],
                    'date': f['fixture']['date'][:16].replace('T', ' ')
                }
                envoyer_notification(opportunities, info, dcs, tier, signal, hxg, axg)
            
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
