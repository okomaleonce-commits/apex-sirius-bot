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
    return "🤖 APEX-ENGINE v2.4 - THE FIX", 200

@app.route('/ping', methods=['GET', 'HEAD'])
def ping():
    return "pong", 200

# ====================== CONFIG ======================
print("🚀 APEX-ENGINE v2.4 - STRATEGIC FIX", flush=True)

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
RHO = 0.10 # Dixon-Coles Rho standard

# Mapping étendu pour éviter le "UNKNOWN"
TIER_P0 = ["uefa champions league", "uefa europa league", "uefa europa conference league"]
TIER_N1 = ["premier league", "championship", "la liga", "laliga", "bundesliga", "ligue 1", "serie a", "eredivisie", "liga portugal", "primeira liga", "jupiler pro league", "scottish premiership"]
TIER_N2 = ["süper lig", "super lig", "russian premier league", "super league 1", "bundesliga autrichienne", "super league suisse", "superliga", "allsvenskan", "eliteserien", "ekstraklasa", "czech first league", "otp bank liga", "liga 1", "hnl", " primera division", "primeira liga"]
TIER_N3 = ["major league soccer", "liga mx", "liga profesional argentina", "brasileirão", "j1 league", "k league 1", "saudi pro league", "chinese super league"]

BLACKLIST_KEYWORDS = ["u17", "u18", "u19", "u20", "u21", "u23", "ii", " b ", "reserves", "youth", "women", "womens", "femenil", "amateur"]

# ====================== HELPERS ======================
def get_league_tier(league_name, country):
    lname = league_name.lower().strip()
    
    # Nettoyage basique
    lname = lname.replace(" - ", " ")
    
    for kw in BLACKLIST_KEYWORDS:
        if kw in lname: return "BLACKLIST"
    
    if any(x in lname for x in TIER_P0): return "P0"
    if any(x in lname for x in TIER_N1): return "N1"
    if any(x in lname for x in TIER_N2): return "N2"
    if any(x in lname for x in TIER_N3): return "N3"
    
    # PRIORITE 5: Bloquer les ligues mortes
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

# ====================== MATH ENGINE (PROPRE) ======================
def poisson_prob(lmbda, k):
    try:
        return (math.exp(-lmbda) * (lmbda ** k)) / math.factorial(k)
    except: return 0.0

def calculate_match_probabilities(hxg, axg):
    """
    Poisson + Dixon-Coles correct.
    Retourne dict: H, D, A, O25, BTTS
    """
    probs = {"H": 0.0, "D": 0.0, "A": 0.0, "O25": 0.0, "BTTS": 0.0}
    if hxg <= 0.1 or axg <= 0.1: return probs # Sécurité

    # Matrices Poisson (0 à 6 buts)
    hp = [poisson_prob(hxg, i) for i in range(7)]
    ap = [poisson_prob(axg, i) for i in range(7)]

    for h in range(7):
        for a in range(7):
            p = hp[h] * ap[a]
            
            # Correction Dixon-Coles (Rho = 0.10)
            # Appliquée seulement sur les scores bas : 0-0, 1-0, 0-1, 1-1
            if h == 0 and a == 0: p *= (1 - RHO)
            elif h == 1 and a == 0: p *= (1 + RHO)
            elif h == 0 and a == 1: p *= (1 + RHO)
            elif h == 1 and a == 1: p *= (1 - RHO)
            
            # Agrégation
            if h > a: probs["H"] += p
            elif h == a: probs["D"] += p
            else: probs["A"] += p
            
            if h + a > 2.5: probs["O25"] += p
            if h >= 1 and a >= 1: probs["BTTS"] += p
            
    return probs

# ====================== VALUE DETECTOR (PRIORITÉ 1 & 3) ======================
def detect_value(probs, odds_data, tier, hxg, axg):
    opps = []
    if not odds_data: return []

    # 1. Trouver les meilleures cotes
    best = {"Home": (0, "N/A"), "Draw": (0, "N/A"), "Away": (0, "N/A")}
    for bm_data in odds_data:
        for bm in bm_data.get('bookmakers', []):
            bn = bm['name']
            for bet in bm['bets']:
                if bet['name'] == "Match Winner":
                    for v in bet['values']:
                        odd = float(v['odd'])
                        side = v['value'] # Home, Draw, Away
                        if odd > best[side][0]:
                            best[side] = (odd, bn)

    # 2. Analyser les marchés
    markets = [
        {"key": "H", "side": "Home"},
        {"key": "D", "side": "Draw"},
        {"key": "A", "side": "Away"}
    ]

    for m in markets:
        prob = probs[m['key']]
        odd, bookie = best[m['side']]
        
        if odd < 1.50: continue # Pas de petites cotes
        
        implied = 1.0 / odd
        
        # PRIORITÉ 3: Nouvelle formule d'Edge
        # Edge = (Prob / Implied) - 1
        # Si Prob=0.30, Implied=0.25 (Cote 4.00) -> Edge = (0.3/0.25)-1 = 0.20 (20% ROI relatif)
        edge = (prob / implied) - 1.0 if implied > 0 else 0
        
        # ========================
        # PRIORITÉ 1: Stopper l'hémorragie sur les DRAWS
        # ========================
        if m['key'] == "D":
            # Filtre A: Proba modèle minimum
            if prob < 0.32: continue 
            # Filtre B: Cote minimum (implied < 31%)
            if odd < 3.20: continue
            
            # PRIORITÉ 2: Filtre équilibre xG
            # Si les équipes ne sont pas proches, le nul est suspect
            if abs(hxg - axg) >= 0.25: continue

        # Seuil d'Edge minimum selon le marché
        # On demande un ROI potentiel de 10% (Edge > 0.10)
        if edge < 0.10: continue

        opps.append({
            "type": "1X2", "label": m['side'].upper(), "odd": odd,
            "edge": edge, "proba_key": m['key'], "bookie": bookie
        })

    # PRIORITÉ 4: Hard filter volume
    return opps[:3] # Max 3 bets par match

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
    msg += f"🚨 VALUE BET\nSelection: {sel}\nOdds: 🚀{opp['odd']:.2f}🚀\nROI Estimé: +{opp['edge']*100:.1f}%"

    try:
        bot.send_message(CHAT_ID, msg)
        print(f"✅ SENT: {sel} @ {opp['odd']:.2f} (ROI +{opp['edge']*100:.0f}%)", flush=True)
    except: pass

# ====================== MAIN CHECK ======================
def check_value_bets():
    if not API_KEY: return
    print(f"\n⏰ Check v2.4", flush=True)

    fixtures = get_fixtures()
    if not fixtures: return
    
    now = datetime.now(timezone.utc)
    
    for f in fixtures:
        try:
            m_date = datetime.fromisoformat(f['fixture']['date'].replace('Z', '+00:00'))
            if not (timedelta(minutes=0) < (m_date - now) < timedelta(hours=6)): continue
        except: continue

        lname = f['league']['name']
        tier = get_league_tier(lname, f['league']['country'])
        
        # PRIORITÉ 5: Bloquer ligues mortes
        if tier == "BLACKLIST" or tier == "UNKNOWN": continue
        
        fid = f['fixture']['id']
        
        s_home = get_team_stats(f['teams']['home']['id'], f['league']['id'], f['league']['season'])
        s_away = get_team_stats(f['teams']['away']['id'], f['league']['id'], f['league']['season'])
        if not s_home or not s_away: continue
        
        # Calcul xG (Fallback API simple, pas de FS mapping pour l'instant)
        try:
            h_avg = s_home['goals']['for']['total']['total'] / s_home['fixtures']['played']['total']
            a_avg = s_away['goals']['for']['total']['total'] / s_away['fixtures']['played']['total']
            # Modèle simple : Attaque * Défense adverse * Avantage Domicile
            hxg = h_avg * 1.15
            axg = a_avg
        except:
            continue

        odds = get_odds(fid)
        probs = calculate_match_probabilities(hxg, axg)
        
        opps = detect_value(probs, odds, tier, hxg, axg)
        
        if opps:
            info = {
                'id': fid, 'league': lname, 
                'home': f['teams']['home']['name'], 'away': f['teams']['away']['name']
            }
            # On envoie que le meilleur bet
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
