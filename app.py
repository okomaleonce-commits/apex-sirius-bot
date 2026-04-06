import requests
import telebot
import time
import schedule
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
    return "🤖 APEX-ENGINE v2.1e - SAFE START", 200

@app.route('/ping', methods=['GET', 'HEAD'])
def ping():
    return "pong", 200

# ====================== CONFIG ======================
print("🚀 APEX-ENGINE v2.1e - STARTING...", flush=True)

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
        print(f"❌ Erreur Telegram init: {e}", flush=True)

BASE_URL = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": API_KEY}
FS_URL = "https://api.footystats.org/v2"

sent_alerts = set()
value_bets_history = []
tracked_bets = []
fs_leagues_cache = {}
fs_teams_cache = {}

# ====================== CONSTANTS ======================
RHO = 0.10
COTE_MIN = 1.40
COTE_MAX = 8.00

# Whitelists
TIER_P0 = ["uefa champions league", "uefa europa league", "uefa europa conference league"]
TIER_N1 = ["premier league", "championship", "league one", "league two", "la liga", "la liga 2", "laliga smartbank", "bundesliga", "2. bundesliga", "3. liga", "ligue 1", "ligue 2", "serie a", "serie b", "liga portugal", "primeira liga", "liga portugal 2", "eredivisie", "eerste divisie", "jupiler pro league", "challenger pro league", "premiership", "scottish championship", "scottish league one"]
TIER_N2 = ["süper lig", "super lig", "tff 1. lig", "russian premier league", "fnl", "ukrainian premier league", "persha liha", "super league 1", "super league 2", "super league greece", "bundesliga autrichienne", "2. liga autrichienne", "super league suisse", "challenge league suisse", "superliga", "1. division", "denmark superliga", "allsvenskan", "superettan", "eliteserien", "1. divisjon", "veikkausliiga", "ekstraklasa", "i liga", "czech first league", "czech national football league", "fortuna liga", "otp bank liga", "nemzeti bajnokság ii", "liga 1", "liga 2", "liga 1 romania", "superliga srbija", "prva liga", "hnl", "1. nl", "prva liga telekom", "prva liga slovenije", "premier league de bosnie", "first professional league", "second professional league", "kategoria superiore", "prva makedonska", "meridianbet", "1. cfl", "1re division chypriote", "cyprus division", "israeli premier league", "liga leumit", "league of ireland", "premier division", "nifl premiership", "cymru premier", "kazakhstan premier league", "azerbaijan premier league"]
TIER_N3 = ["major league soccer", "usl championship", "liga mx", "liga de expansión mx", "liga profesional argentina", "primera nacional", "brasileirão série a", "brasileirão série b", "serie a brazil", "serie b brazil", "chilean primera división", "colombian primera a", "liga 1 perú", "campeonato uruguayo", "ligapro ecuador", "copa libertadores", "copa sudamericana", "j1 league", "j2 league", "k league 1", "k league 2", "chinese super league", "china super league", "chinese league one", "indian super league", "saudi pro league", "roshn saudi league", "uae arabian gulf league", "qatar stars league", "persian gulf pro league", "thai league 1", "malaysian super league", "singapore premier league", "v.league 1", "a-league", "a-league men", "nrfl", "afc champions league", "afc champions league elite", "afc cup"]
TIER_N4 = ["botola pro", "caf champions league", "caf confederation cup", "egyptian premier league", "tunisian ligue professionnelle 1", "algerian ligue professionnelle 1", "premier soccer league", "psl", "libyan premier league", "nigerian premier football league", "kenyan premier league", "tanzanian premier league", "ugandan super league", "zambia super league", "zimbabwean premier soccer league", "cameroon elite one", "senegalese ligue 1", "mtn ligue 1", "côte d'ivoire", "ghanaian premier league", "jordan pro league", "lebanese premier league", "iraqi premier league", "bahraini premier league", "omani professional league", "kuwaiti premier league", "lithuanian a lyga", "latvian higher league", "estonian meistriliiga", "belarusian premier league", "moldovan national division", "georgian erovnuli liga", "armenian premier league"]

BLACKLIST_KEYWORDS = ["u17", "u18", "u19", "u20", "u21", "u23", "ii", " b team", " b ", "reserves", "youth", "primavera", "jong", "amateur", "development", "academy", "filial", "reserve", "juniores", "sub-", "women", "womens", "femenil", "femenino"]

DCS_MIN_TIERS = { "P0": 65, "N1": 65, "N2": 70, "N3": 75, "N4": 78 }
MARGE_MAX_TIERS = { "P0": 0.07, "N1": 0.09, "N2": 0.11, "N3": 0.12, "N4": 0.13 }
EDGE_MIN_TIERS  = { "P0": 0.05, "N1": 0.05, "N2": 0.05, "N3": 0.06, "N4": 0.07 }

# ====================== FOOTYSTATS BRIDGE ======================
def init_footystats():
    global fs_leagues_cache
    print("🔄 [BG] Chargement FootyStats...", flush=True)
    try:
        url = f"{FS_URL}/leagues?key={FOOTYSTATS_KEY}"
        resp = requests.get(url, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            cache = {}
            for item in data.get('data', []):
                name = item.get('name', '').lower()
                country = item.get('country', '').lower()
                lid = item.get('id')
                cache[name] = lid
                cache[f"{country} {name}"] = lid
            fs_leagues_cache = cache
            print(f"✅ [BG] FootyStats: {len(cache)} ligues chargées.", flush=True)
        else:
            print("❌ [BG] Erreur FootyStats", flush=True)
    except Exception as e:
        print(f"❌ [BG] Exception FootyStats: {e}", flush=True)

def get_fs_team_id(team_name):
    global fs_teams_cache
    if team_name in fs_teams_cache:
        return fs_teams_cache[team_name]
    try:
        url = f"{FS_URL}/search?key={FOOTYSTATS_KEY}&search_term={team_name}"
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            for item in data.get('data', []):
                if item.get('type') == 'team':
                    if item.get('name', '').lower() == team_name.lower():
                        tid = item.get('id')
                        fs_teams_cache[team_name] = tid
                        return tid
    except:
        pass
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
            if played > 0 and xg_for is not None and xg_ag is not None:
                return xg_for / played, xg_ag / played
    except:
        pass
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
def calculate_smart_xg(stats):
    try:
        played = stats.get('fixtures', {}).get('played', {}).get('total', 1)
        if played == 0: played = 1
        shots = stats.get('shots', {})
        shots_on = shots.get('on', {}).get('total', 0)
        shots_total = shots.get('total', {}).get('total', 0)
        if shots_total > 0:
            avg_shots_on = shots_on / played
            xg_shots = avg_shots_on * 0.30
            xg_volume = (shots_total / played) * 0.03
            return xg_shots + xg_volume
        goals_for = stats.get('goals', {}).get('for', {}).get('total', {}).get('total', 0)
        return goals_for / played
    except: return 1.3

def calculate_strength_model(stats_home, stats_away, league_avg, fs_data=None):
    hxg, axg = None, None
    if fs_data:
        fs_hxg = fs_data.get('home_xg')
        fs_hxga = fs_data.get('home_xga')
        fs_axg = fs_data.get('away_xg')
        fs_axga = fs_data.get('away_xga')
        if all([fs_hxg, fs_hxga, fs_axg, fs_axga]):
            hxg = fs_hxg * 1.10
            axg = fs_axg
            return hxg, axg

    home_attack = calculate_smart_xg(stats_home)
    away_attack = calculate_smart_xg(stats_away)
    home_def_raw = stats_home.get('goals', {}).get('against', {}).get('total', {}).get('total', 0)
    away_def_raw = stats_away.get('goals', {}).get('against', {}).get('total', {}).get('total', 0)
    played_h = stats_home.get('fixtures', {}).get('played', {}).get('total', 1)
    played_a = stats_away.get('fixtures', {}).get('played', {}).get('total', 1)
    
    home_def_avg = home_def_raw / played_h
    away_def_avg = away_def_raw / played_a
    
    home_str = home_attack / league_avg if league_avg > 0 else 1
    home_def_str = home_def_avg / league_avg if league_avg > 0 else 1
    away_str = away_attack / league_avg if league_avg > 0 else 1
    away_def_str = away_def_avg / league_avg if league_avg > 0 else 1
    
    hxg = home_str * away_def_str * league_avg * 1.15
    axg = away_str * home_def_str * league_avg
    return hxg, axg

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

def poisson_prob(l, k):
    try: return (math.exp(-l) * (l ** k)) / math.factorial(k)
    except: return 0

# ====================== MARKET ANALYZER ======================
def analyze_markets(model_probs, odds_data, tier):
    opportunities = []
    if not odds_data: return []
    best_odds = {"Home": 0, "Draw": 0, "Away": 0, "Over 2.5": 0}
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
                if bet['name'] == "Goals Over/Under":
                    for v in bet['values']:
                        odd = float(v['odd'])
                        if "Over 2.5" in v['value'] and odd > best_odds['Over 2.5']:
                            best_odds['Over 2.5'] = odd
                            best_bookie['Over 2.5'] = bm_name
    edge_min = EDGE_MIN_TIERS[tier]
    for key, prob_key in [("Home", "H"), ("Draw", "D"), ("Away", "A")]:
        odd = best_odds[key]
        if odd >= COTE_MIN and odd <= COTE_MAX:
            implied = 1/odd
            edge = model_probs[prob_key] - implied
            if edge > edge_min:
                opportunities.append({"type": "1X2", "label": key.upper(), "odd": odd, "edge": edge, "proba_key": prob_key, "is_value": True, "bookie": best_bookie.get(key, "N/A")})
    odd = best_odds['Over 2.5']
    if odd >= COTE_MIN:
        implied = 1/odd
        edge = model_probs['O25'] - implied
        if edge > edge_min:
             opportunities.append({"type": "OU", "label": "Over 2.5", "odd": odd, "edge": edge, "proba_key": "O25", "is_value": True, "bookie": best_bookie.get('Over 2.5', "N/A")})
    return opportunities

# ====================== ROI TRACKER ======================
def calculate_roi_report(finished_bets):
    if not finished_bets: return ""
    total_stake = len(finished_bets)
    total_profit = 0
    wins = 0
    for bet in finished_bets:
        if bet['is_win']:
            wins += 1
            total_profit += (bet['odd'] - 1)
        else:
            total_profit -= 1
    roi = (total_profit / total_stake) * 100 if total_stake > 0 else 0
    win_rate = (wins / total_stake) * 100 if total_stake > 0 else 0
    return f"""
📊 PERFORMANCE REPORT 📊
----------------------------
📈 Total Paris: {total_stake}
✅ Gagnés: {wins} ({win_rate:.1f}%)
💰 Profit Net: {total_profit:.2f}u
📉 ROI: {roi:.2f}%
----------------------------
"""

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
                profit = 0
                if is_win:
                    profit = bet['odd'] - 1
                    result_icon = f"✅ GAGNÉ (+{profit:.2f}u)"
                else:
                    profit = -1
                    result_icon = f"❌ PERDU (-1.00u)"
                finished_bets.append({'is_win': is_win, 'odd': bet['odd']})
                line = f"📅 {bet['date']}\n🏆 {bet['league']}\n⚽ {bet['home']} vs {bet['away']}\n🔮 Signal: {bet['signal_name']} @ {bet['odd']:.2f}\n🏁 Score: {score_home} - {score_away}\n{result_icon}\n------------------------"
                report_lines.append(line)
            else:
                still_pending.append(bet)
        else:
            still_pending.append(bet)
    tracked_bets = still_pending
    if report_lines:
        header = "📊 RÉSULTATS DES PRÉCÉDENTS PARIS 📊\n\n"
        roi_stats = calculate_roi_report(finished_bets)
        full_report = header + "\n".join(report_lines) + roi_stats
        try:
            if bot: bot.send_message(CHAT_ID, full_report)
        except: pass

# ====================== NOTIFICATION ======================
def get_strong_signal(home_xg, away_xg, probs):
    xg_diff = home_xg - away_xg
    total_xg = home_xg + away_xg
    if xg_diff > 1.2: return f"🛡️ DOMINANCE DOMICILE (xG {home_xg:.2f} vs {away_xg:.2f})"
    elif xg_diff < -1.2: return f"🛡️ DOMINANCE EXTERIEURE (xG {away_xg:.2f} vs {home_xg:.2f})"
    if total_xg > 3.5: return f"🔥 MATCH OUVERT (xG Total {total_xg:.2f})"
    if total_xg < 2.0: return f"🧱 MATCH FERME (xG Total {total_xg:.2f})"
    return "⚖️ EQUILIBRE TACTIQUE"

def envoyer_notification(opps, fixture_info, dcs, tier, strong_signal, hxg, axg, source="API-Football"):
    if not bot: return
    fid = fixture_info['id']
    key = f"{fid}"
    if key in sent_alerts: return
    sent_alerts.add(key)
    value_bets = [o for o in opps if o['is_value']]
    msg = f"⚽ SOCCER ⚽\n\n{fixture_info['home']} vs {fixture_info['away']}\n🌍 {fixture_info['country']} - {fixture_info['league']}\n🕒 {fixture_info['date']} (UTC)\n\n{strong_signal}\n📊 xG Model ({source}): {hxg:.2f} - {axg:.2f}\n"
    if value_bets:
        main = value_bets[0]
        selection_name = main['label']
        if main['type'] == "1X2":
            if main['label'] == "HOME WIN": selection_name = fixture_info['home']
            elif main['label'] == "AWAY WIN": selection_name = fixture_info['away']
            else: selection_name = "Draw"
        msg += f"\n🚨 VALUE BET DETECTED 🚨\nSelection: {selection_name}\nMin. Odds: 🚀{main['odd']:.2f}🚀\nEdge: +{main['edge']*100:.1f}%\nBookie: {main.get('bookie', 'Best Market')}\n"
        tracked_bets.append({
            "fid": fid, "home": fixture_info['home'], "away": fixture_info['away'],
            "league": fixture_info['league'], "date": fixture_info['date'],
            "signal_name": selection_name, "prediction_type": main['proba_key'], "odd": main['odd']
        })
    else:
        msg += "\n📉 VALUE BET: Aucune value significative.\n"
    try:
        bot.send_message(CHAT_ID, msg)
        print(f"✅ Telegram envoyé pour {fid} (Source: {source})", flush=True)
    except:
        pass

# ====================== CHECK MAIN ======================
def check_value_bets():
    if not API_KEY: return
    print(f"\n⏰ Check v2.1e à {datetime.now(timezone.utc).strftime('%H:%M:%S')}", flush=True)
    
    # Init FootyStats ici si pas encore fait (lazy loading)
    if not fs_leagues_cache:
        init_footystats()

    fixtures = get_fixtures()
    if not fixtures: return
    check_results(fixtures)
    
    now = datetime.now(timezone.utc)
    count = 0
    league_avg = 2.70
    
    for f in fixtures:
        try:
            m_date = datetime.fromisoformat(f['fixture']['date'].replace('Z', '+00:00'))
            if not (timedelta(minutes=0) < (m_date - now) < timedelta(minutes=60)): continue
        except: continue
        lname = f['league']['name']
        country = f['league']['country']
        tier = get_league_tier(lname, country)
        if tier in ["BLACKLIST", "UNKNOWN"]: continue
        if count >= 15: break
        count += 1
        
        fid = f['fixture']['id']
        lid = f['league']['id']
        season = f['league']['season']
        ht = f['teams']['home']
        at = f['teams']['away']
        
        s_home = get_team_stats(ht['id'], lid, season)
        s_away = get_team_stats(at['id'], lid, season)
        if not s_home or not s_away: continue
        
        fs_data = {'home_xg': None, 'home_xga': None, 'away_xg': None, 'away_xga': None}
        data_source = "API-Football"
        
        if FOOTYSTATS_KEY:
            tid_home = get_fs_team_id(ht['name'])
            tid_away = get_fs_team_id(at['name'])
            if tid_home and tid_away:
                xg_h, xga_h = get_footystats_xg(tid_home)
                xg_a, xga_a = get_footystats_xg(tid_away)
                if xg_h and xga_a:
                    fs_data['home_xg'] = xg_h
                    fs_data['
