import requests
import telebot
import time
import os
import threading
import math
import csv
from flask import Flask
from datetime import datetime, timezone, timedelta

print("🚀 APEX-SIRIUS v5.0 - HYBRID INTELLIGENCE", flush=True)

# ====================== FLASK ======================
app = Flask(__name__)

@app.route('/')
def home():
    return "🤖 APEX-SIRIUS v5.0 Running", 200

@app.route('/ping')
def ping():
    return "pong", 200

# ====================== CONFIG ======================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
API_KEY = os.environ.get("API_KEY")
FOOTYSTATS_KEY = os.environ.get("FOOTYSTATS_KEY")

bot = None
if all([BOT_TOKEN, CHAT_ID, API_KEY, FOOTYSTATS_KEY]):
    try:
        bot = telebot.TeleBot(BOT_TOKEN)
        print("✅ Telegram Bot initialisé", flush=True)
    except Exception as e:
        print(f"❌ Erreur Telegram: {e}", flush=True)
else:
    print("❌ Variables manquantes", flush=True)

BASE_URL = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": API_KEY}
FS_URL = "https://api.footystats.org/v2"

# ====================== TRACKING & ROI ======================
TRACKING_FILE = "/tmp/apex_roi_v5.csv"
BANKROLL_FILE = "/tmp/bankroll.txt"
INITIAL_BANKROLL = 100.0

def get_bankroll():
    try:
        with open(BANKROLL_FILE, "r") as f:
            return float(f.read())
    except:
        with open(BANKROLL_FILE, "w") as f:
            f.write(str(INITIAL_BANKROLL))
        return INITIAL_BANKROLL

def update_bankroll(amount):
    current = get_bankroll()
    new_val = current + amount
    with open(BANKROLL_FILE, "w") as f:
        f.write(f"{new_val:.2f}")

def log_bet(data):
    try:
        with open(TRACKING_FILE, "a", newline='') as f:
            w = csv.writer(f)
            w.writerow(data)
    except: pass

# ====================== ML & FEATURES ======================
def get_team_form(last5): # Simple ML Feature: Form Points
    # Convertit "W,D,L,L,W" en points (3,1,0)
    # Implement real parsing if API provides, else return neutral
    return 0 

def calculate_confidence(hxg, axg, league_tier, odds_value):
    """
    Light ML: Scoring heuristique.
    Score élevé = Confiance forte.
    """
    score = 0
    # 1. Gap de qualité (xG Diff)
    diff = abs(hxg - axg)
    if diff > 0.8: score += 20
    if diff > 1.5: score += 10
    
    # 2. Qualité Ligue
    if league_tier in ["P0", "N1"]: score += 15 # Données plus fiables
    
    # 3. Value Edge
    if odds_value > 0.05: score += 10
    if odds_value > 0.10: score += 5
    
    return score # Max ~50-60

# ====================== FOOTYSTATS BRIDGE ======================
fs_cache = {}
def get_fs_xg(team_name):
    if team_name in fs_cache: return fs_cache[team_name]
    try:
        r = requests.get(f"{FS_URL}/search?key={FOOTYSTATS_KEY}&search_term={team_name}", timeout=5)
        if r.status_code == 200:
            data = r.json().get('data', [])
            if data:
                # Prendre le 1er résultat team
                tid = data[0]['id']
                # Récupérer stats
                r2 = requests.get(f"{FS_URL}/team?key={FOOTYSTATS_KEY}&team_id={tid}", timeout=5)
                if r2.status_code == 200:
                    stats = r2.json().get('data', {}).get('xG', {})
                    # Moyenne saison xG
                    xg_for = stats.get('total_xG', 0)
                    played = stats.get('matches_played', 1)
                    val = xg_for / played if played > 0 else 1.2
                    fs_cache[team_name] = val
                    return val
    except: pass
    return None

# ====================== MATH ======================
def poisson_prob(lmbda, k):
    try: return (math.exp(-lmbda) * (lmbda ** k)) / math.factorial(k)
    except: return 0.0

def calculate_probs(hxg, axg):
    probs = {"H": 0.0, "D": 0.0, "A": 0.0}
    hp = [poisson_prob(hxg, i) for i in range(7)]
    ap = [poisson_prob(axg, i) for i in range(7)]
    for h in range(7):
        for a in range(7):
            p = hp[h] * ap[a]
            if h == 0 and a == 0: p *= 0.96
            elif h == 1 and a == 0: p *= 1.04
            elif h == 0 and a == 1: p *= 1.04
            if h > a: probs["H"] += p
            elif h == a: probs["D"] += p
            else: probs["A"] += p
    return probs

# ====================== VALUE ENGINE ======================
def detect_best_value(probs, odds_data, hxg, axg, tier):
    best = None
    max_edge = 0.0
    
    for bm_data in odds_data:
        for bm in bm_data.get('bookmakers', []):
            bn = bm['name']
            for bet in bm.get('bets', []):
                if bet['name'] == "Match Winner":
                    for v in bet.get('values', []):
                        side = v['value']
                        odd = float(v['odd'])
                        if odd < 1.50: continue
                        
                        key = "H" if side == "Home" else "D" if side == "Draw" else "A"
                        prob_model = probs[key]
                        
                        # --- ML WEIGHTING ---
                        # Si FootyStats absent, on réduit la confiance
                        conf = calculate_confidence(hxg, axg, tier, prob_model - (1/odd))
                        
                        edge = prob_model - (1/odd)
                        
                        # --- FILTER LOSING MARKETS ---
                        # 1. Draws filtrés sévèrement (Trop volatile)
                        if key == "D" and edge < 0.07: continue 
                        
                        # 2. Outsiders filtrés si pas de domination réelle
                        if key == "A" and hxg > axg and odd > 4.0: continue
                        
                        # 3. Favoris filtrés si sur-côtés
                        if key == "H" and hxg < axg and odd < 1.80: continue

                        if edge > max_edge:
                            max_edge = edge
                            best = {
                                "side": side, "odd": odd, "edge": edge, 
                                "bookie": bn, "conf": conf
                            }
    return best

# ====================== API ======================
def safe_get(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        return r.json() if r.status_code == 200 else None
    except: return None

def get_fixtures():
    d = safe_get(f"{BASE_URL}/fixtures?date={time.strftime('%Y-%m-%d')}")
    return d.get('response', []) if d else []

def get_odds(fid):
    d = safe_get(f"{BASE_URL}/odds?fixture={fid}")
    return d.get('response', []) if d else []

def get_stats(tid, lid, season):
    d = safe_get(f"{BASE_URL}/teams/statistics?team={tid}&league={lid}&season={season}")
    return d.get('response') if d else None

# ====================== CHECK ======================
def check_loop():
    print(f"\n⏰ v5.0 Check at {datetime.now().strftime('%H:%M')}", flush=True)
    
    fixtures = get_fixtures()
    now = datetime.now(timezone.utc)
    bank = get_bankroll()
    sent = 0
    
    for f in fixtures:
        try:
            m_date = datetime.fromisoformat(f['fixture']['date'].replace('Z', '+00:00'))
            delta_h = (m_date - now).total_seconds() / 3600
            
            # --- TIMING DE MARCHÉ ---
            # On vise les early odds (2h-6h avant) ou late movement (0-1h)
            if not (0 < delta_h < 6): continue
            
            # Blacklist
            h_name = f['teams']['home']['name']
            a_name = f['teams']['away']['name']
            if any(x in h_name.lower() or x in a_name.lower() for x in ["women", " w", "u19", "reserves"]): continue
            
            # Data
            stats_h = get_stats(f['teams']['home']['id'], f['league']['id'], f['league']['season'])
            stats_a = get_stats(f['teams']['away']['id'], f['league']['id'], f['league']['season'])
            if not stats_h or not stats_a: continue
            
            # --- HYBRID XG ---
            # 1. Essayer FootyStats
            hxg_fs = get_fs_xg(h_name)
            axg_fs = get_fs_xg(a_name)
            
            # 2. Fallback API-Football
            if not hxg_fs:
                h_g = stats_h['goals']['for']['total']['total'] / stats_h['fixtures']['played']['total']
                hxg_fs = h_g * 1.10 # Home adv
            
            if not axg_fs:
                a_g = stats_a['goals']['for']['total']['total'] / stats_a['fixtures']['played']['total']
                axg_fs = a_g

            # Calcul Probas
            probs = calculate_probs(hxg_fs, axg_fs)
            
            odds_data = get_odds(f['fixture']['id'])
            if not odds_data: continue
            
            best = detect_best_value(probs, odds_data, hxg_fs, axg_fs, "N1")
            
            if best and sent < 8:
                # Envoi
                conf_score = best['conf']
                msg = f"""🚀 HYBRID BET v5.0
{h_name} vs {a_name}
🎯 {best['side']} @ {best['odd']:.2f} ({best['bookie']})
💰 Edge: +{best['edge']*100:.1f}%
🧠 ML Score: {conf_score}/50
🏦 Bankroll: {bank:.1f}u"""
                
                try:
                    bot.send_message(CHAT_ID, msg)
                    print(f"✅ Sent: {best['side']} @ {best['odd']}", flush=True)
                    
                    # Log pour ROI & CLV
                    log_bet([
                        datetime.now().isoformat(), f['fixture']['id'], h_name, a_name,
                        best['side'], best['odd'], best['edge'], best['bookie'], hxg_fs, axg_fs
                    ])
                    sent += 1
                except: pass
                
        except Exception as e:
            print(f"⚠️ Err: {e}", flush=True)
            
    print(f"✅ Done. Bankroll: {bank:.1f}u", flush=True)

# ====================== ROI CHECKER (BG THREAD) ======================
def check_results():
    # Cette fonction est appelée périodiquement pour checker les résultats passés
    # Pour l'instant on log, le calcul ROI complet se fera sur dashboard externe ou logs
    pass

# ====================== SCHEDULER ======================
def run():
    time.sleep(15)
    check_loop()
    schedule.every(15).minutes.do(check_loop)
    while True:
        schedule.run_pending()
        time.sleep(1)

if bot:
    threading.Thread(target=run, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
