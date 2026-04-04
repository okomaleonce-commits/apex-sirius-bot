import requests
import telebot
import time
import schedule
import os
import sys
import threading
from flask import Flask, render_template_string
from datetime import datetime, timezone, timedelta

print("🚀 APEX-SIRIUS vPRO-UNLIMITED - TOUTES LIGUES AUTORISÉES", flush=True)

# ====================== CONFIG ======================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
API_KEY = os.environ.get("API_KEY")

if not all([BOT_TOKEN, CHAT_ID, API_KEY]):
    print("❌ ERREUR CRITIQUE: Variables manquantes", flush=True)

bot = telebot.TeleBot(BOT_TOKEN)
BASE_URL = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": API_KEY}

sent_alerts = set()
value_bets_history = []

# ====================== FILTRE LIGUES (MODE NOIR) ======================
# On ignore SEULEMENT les ligues inintéressantes (Amicaux, Jeunes, Féminines si tu veux, etc.)
# On garde TOUT le reste (D2, D3, ligues exotiques) car c'est là qu'il y a de la valeur.
BLACKLIST_LEAGUES = {
    "Friendlies", "Club Friendlies", "Youth Leagues", "U20", "U19", "U18", "U17",
    "UEFA Youth League", "Premier League 2", "MLS Next Pro"
}

# ====================== API ======================
def safe_api_call(url):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code == 200:
            return resp.json()
        elif resp.status_code == 429:
            print("🛑 QUOTA ATTEINT (429) - Pause 1min", flush=True)
            time.sleep(60)
        else:
            print(f"⚠️ API Erreur {resp.status_code}", flush=True)
    except Exception as e:
        print(f"⚠️ Exception API: {e}", flush=True)
    return None

def get_fixtures():
    today = time.strftime("%Y-%m-%d")
    url = f"{BASE_URL}/fixtures?date={today}"
    data = safe_api_call(url)
    return data.get('response', []) if data else []

def get_predictions(fixture_id):
    url = f"{BASE_URL}/predictions?fixture={fixture_id}"
    data = safe_api_call(url)
    response = data.get('response', []) if data else []
    return response[0] if response else None

def get_odds(fixture_id):
    url = f"{BASE_URL}/odds?fixture={fixture_id}"
    data = safe_api_call(url)
    return data.get('response', []) if data else []

def get_injuries(fixture_id):
    url = f"{BASE_URL}/injuries?fixture={fixture_id}"
    data = safe_api_call(url)
    if not data or not data.get('response'):
        return "✅ Effectifs complets"
    injuries = data['response']
    return f"🩹 {len(injuries)} blessures signalées"

# ====================== CALCUL VALUE BET ======================
def calcul_value_bet(odds_data, prediction, fixture):
    if not odds_data or not prediction:
        return None

    values = []
    try:
        preds = prediction.get('predictions', {})
        percent = preds.get('percent', {})

        def get_pct(key):
            val = percent.get(key, '0')
            return float(str(val).replace('%', '')) / 100

        pred_home = get_pct('home')
        pred_draw = get_pct('draw')
        pred_away = get_pct('away')

        if pred_home + pred_draw + pred_away == 0:
            return None

        edge = 0.03 # 3% minimum

        # Vérification sécurité structure odds
        if not odds_data or not odds_data[0].get('bookmakers'):
            return None

        for bm in odds_data[0].get('bookmakers', []):
            if bm['name'].lower() not in ['pinnacle', 'betway', 'bet365', '1xbet']:
                continue
            for bet_group in bm['bets']:
                name = bet_group['name']
                vals = bet_group['values']

                if name == "Match Winner":
                    for v in vals:
                        odd = float(v['odd'])
                        if odd < 1.50: continue
                        implied = 1 / odd
                        
                        if v['value'] == 'Home' and (pred_home - implied) > edge:
                            values.append(f"🏠 HOME VALUE : {pred_home*100:.1f}% vs Cote {odd}")
                        elif v['value'] == 'Draw' and (pred_draw - implied) > edge:
                            values.append(f"⚖️ DRAW VALUE : {pred_draw*100:.1f}% vs Cote {odd}")
                        elif v['value'] == 'Away' and (pred_away - implied) > edge:
                            values.append(f"🏃 AWAY VALUE : {pred_away*100:.1f}% vs Cote {odd}")
                
    except Exception as e:
        print(f"❌ Erreur calcul: {e}", flush=True)

    return "\n".join(values) if values else None

# ====================== NOTIFICATION ======================
def envoyer_notification(message, fixture_id, country, league, date_time):
    # Clé unique pour éviter le spam continu sur le même match
    alert_key = f"{fixture_id}_{date_time}" 
    if alert_key in sent_alerts:
        return
    sent_alerts.add(alert_key)

    full_msg = f"""🚨 APEX-SIRIUS VALUE BET

🌍 {country} | 🏆 {league}
🕒 Kick-off: {date_time} (UTC)

{message}"""

    try:
        bot.send_message(CHAT_ID, full_msg)
        print(f"✅ Telegram envoyé pour {fixture_id}", flush=True)
        value_bets_history.append({"time": datetime.now().strftime("%H:%M"), "message": full_msg})
        if len(value_bets_history) > 50: value_bets_history.pop(0)
    except Exception as e:
        print(f"❌ Erreur Telegram: {e}", flush=True)

# ====================== CHECK ======================
def check_value_bets():
    print(f"\n⏰ Check PRE-MATCH à {datetime.now(timezone.utc).strftime('%H:%M:%S')} (UTC)", flush=True)
    fixtures = get_fixtures()
    if not fixtures: return
    
    print(f"📊 {len(fixtures)} matchs chargés.", flush=True)

    now = datetime.now(timezone.utc)
    candidates = []

    # 1. FILTRAGE TEMPOREL
    for fixture in fixtures:
        status = fixture['fixture']['status']['short']
        if status not in ["NS", "TBD"]: # NS = Not Started
            continue

        try:
            match_date = datetime.fromisoformat(fixture['fixture']['date'].replace('Z', '+00:00'))
            
            # Matchs dans moins de 1h
            time_until_start = match_date - now
            
            if timedelta(minutes=0) < time_until_start < timedelta(minutes=60):
                candidates.append({'fixture': fixture, 'date': match_date})
        except:
            continue

    # 2. TRI PAR DATE
    candidates.sort(key=lambda x: x['date'])

    print(f"🎯 {len(candidates)} matchs PRE-MATCH détectés.", flush=True)

    count_analyzed = 0
    count_value = 0
    
    for item in candidates:
        fixture = item['fixture']
        fid = fixture['fixture']['id']
        
        league_name = fixture.get('league', {}).get('name', 'Inconnu')
        country = fixture.get('league', {}).get('country', 'Inconnu')
        
        # ====================== FILTRE NOIR ======================
        # On ignore les ligues de la blackliste
        if any(bl.lower() in league_name.lower() for bl in BLACKLIST_LEAGUES):
            continue
        # =========================================================

        count_analyzed += 1
        home = fixture['teams']['home']['name']
        away = fixture['teams']['away']['name']
        date_time = fixture['fixture']['date'][:16].replace('T', ' ')
        
        print(f"⏳ Analyse: {league_name} - {home} vs {away}", flush=True)

        pred = get_predictions(fid)
        odds = get_odds(fid)

        if pred and odds:
            value_msg = calcul_value_bet(odds, pred, fid)
            if value_msg:
                count_value += 1
                injuries = get_injuries(fid) 
                msg = f"{home} vs {away}\n\n{value_msg}\n\n{injuries}"
                envoyer_notification(msg, fid, country, league_name, date_time)
        
        time.sleep(0.5)

    print(f"✅ Terminé: {count_analyzed} analysés | {count_value} alertes.", flush=True)

# ====================== FLASK ======================
app = Flask(__name__)

@app.route('/')
def home():
    return "🤖 APEX-SIRIUS PRO UNLIMITED RUNNING", 200

@app.route('/ping', methods=['GET', 'HEAD'])
def ping():
    return "pong", 200

@app.route('/dashboard')
def dashboard():
    html = """
    <html><head><title>Dashboard</title>
    <meta http-equiv="refresh" content="60">
    <style>body{font-family:Arial;background:#111;color:#eee;padding:20px;}</style></head>
    <body><h1>Dashboard</h1>
    {% for bet in history %}<div><b>{{ bet.time }}</b><pre>{{ bet.message }}</pre></div><hr>{% endfor %}
    </body></html>
    """
    return render_template_string(html, history=value_bets_history[::-1])

def run_scheduler():
    print("🗓️ Scheduler actif...", flush=True)
    time.sleep(5)
    check_value_bets()
    schedule.every(15).minutes.do(check_value_bets)
    while True:
        schedule.run_pending()
        time.sleep(1)

threading.Thread(target=run_scheduler, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
