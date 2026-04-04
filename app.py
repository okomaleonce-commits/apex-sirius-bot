import requests
import telebot
import time
import schedule
import os
import sys
import threading
from flask import Flask, render_template_string
from datetime import datetime, timezone, timedelta

print("🚀 APEX-SIRIUS vPROD-STABLE - QUOTA SAVER + FILTRE LIGUES", flush=True)

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
debug_structure_logged = False

# ====================== FILTRE LIGUES ======================
# Seules ces ligues seront analysées
ALLOWED_LEAGUES = {
    "Premier League", "Championship", "La Liga", "Segunda División",
    "Bundesliga", "2. Bundesliga", "Serie A", "Serie B",
    "Ligue 1", "Ligue 2", "Eredivisie", "Primeira Liga", 
    "Champions League", "Europa League", "Pro League", "Liga Portugal"
}

# ====================== API ======================
def safe_api_call(url):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=12)
        if resp.status_code == 200:
            return resp.json()
        elif resp.status_code == 429:
            print("🛑 QUOTA ATTEINT (429)", flush=True)
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

# ====================== CALCUL VALUE BET ======================
def calcul_value_bet(odds_data, prediction, fixture):
    global debug_structure_logged
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

        edge = 0.03 # Seuil de 3% pour éviter les faux positifs

        for bm in odds_data[0].get('bookmakers', []):
            if bm['name'].lower() not in ['pinnacle', 'betway', 'bet365']:
                continue
            for bet_group in bm['bets']:
                name = bet_group['name']
                vals = bet_group['values']

                if name == "Match Winner":
                    for v in vals:
                        odd = float(v['odd'])
                        if odd < 1.50: continue # Cotes trop basses ignorées
                        implied = 1 / odd
                        
                        if v['value'] == 'Home' and (pred_home - implied) > edge:
                            values.append(f"🏠 HOME VALUE : {pred_home*100:.1f}% vs {odd}")
                        elif v['value'] == 'Draw' and (pred_draw - implied) > edge:
                            values.append(f"⚖️ DRAW VALUE : {pred_draw*100:.1f}% vs {odd}")
                        elif v['value'] == 'Away' and (pred_away - implied) > edge:
                            values.append(f"🏃 AWAY VALUE : {pred_away*100:.1f}% vs {odd}")
    except Exception as e:
        print(f"❌ Erreur calcul: {e}", flush=True)

    return "\n".join(values) if values else None

# ====================== NOTIFICATION ======================
def envoyer_notification(message, fixture_id, country, league, date_time):
    alert_key = f"{fixture_id}_{hash(message)}"
    if alert_key in sent_alerts: return
    sent_alerts.add(alert_key)

    full_msg = f"""🚨 APEX-SIRIUS VALUE BET

🌍 {country} | 🏆 {league}
🕒 {date_time}

{message}"""

    try:
        bot.send_message(CHAT_ID, full_msg)
        print(f"✅ Telegram envoyé pour {fixture_id}", flush=True)
        value_bets_history.append({"time": datetime.now().strftime("%H:%M"), "message": full_msg})
        if len(value_bets_history) > 30: value_bets_history.pop(0)
    except Exception as e:
        print(f"❌ Erreur Telegram: {e}", flush=True)

# ====================== CHECK ======================
def check_value_bets():
    print(f"\n⏰ Check lancé à {datetime.now().strftime('%H:%M:%S')}", flush=True)
    fixtures = get_fixtures()
    if not fixtures: return
    
    print(f"📊 {len(fixtures)} matchs chargés.", flush=True)

    now = datetime.now(timezone.utc)
    candidates = []

    # 1. Filtrage Temporel
    for fixture in fixtures:
        status = fixture['fixture']['status']['short']
        if status in ['FT', 'AET', 'PEN', 'CANC', 'PST', 'ABD']: continue

        try:
            match_date = datetime.fromisoformat(fixture['fixture']['date'].replace('Z', '+00:00'))
            # Garde matchs dans les 2h à venir ou commencé il y a moins de 2h
            if (match_date > now - timedelta(hours=2)) and (match_date < now + timedelta(hours=3)):
                candidates.append({'fixture': fixture, 'date': match_date})
        except:
            continue

    # 2. Tri par date
    candidates.sort(key=lambda x: x['date'])
    print(f"🗓️ {len(candidates)} matchs temporellement éligibles.", flush=True)

    count_analyzed = 0
    count_value = 0
    
    # 3. Analyse (LIMITÉ À 10 pour QUOTA)
    LIMIT = 10 
    
    for item in candidates[:LIMIT]:
        fixture = item['fixture']
        fid = fixture['fixture']['id']
        
        league_name = fixture.get('league', {}).get('name', 'Inconnu')
        country = fixture.get('league', {}).get('country', 'Inconnu')
        
        # ====================== FILTRE LIGUE RÉACTIVÉ ======================
        if league_name not in ALLOWED_LEAGUES:
            continue
        # ==================================================================

        count_analyzed += 1
        home = fixture['teams']['home']['name']
        away = fixture['teams']['away']['name']
        date_time = fixture['fixture']['date'][:16].replace('T', ' ')
        
        print(f"-> Analyse: {league_name} - {home} vs {away}", flush=True)

        pred = get_predictions(fid)
        odds = get_odds(fid)

        if pred and odds:
            value_msg = calcul_value_bet(odds, pred, fid)
            if value_msg:
                count_value += 1
                msg = f"{home} vs {away}\n\n{value_msg}"
                envoyer_notification(msg, fid, country, league_name, date_time)
        
        time.sleep(0.5)

    print(f"✅ Terminé: {count_analyzed} analysés | {count_value} alertes.", flush=True)

# ====================== FLASK ======================
app = Flask(__name__)

@app.route('/')
def home():
    return "🤖 APEX-SIRIUS RUNNING", 200

# CORRECTION ERREUR 404 UPTIMEROBOT
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
    # Toutes les 30 minutes = 48 checks/jour. 
    # 10 matchs/check = 20 req * 48 = 960 req/jour. 
    # ATTENTION: C'est juste sous la limite de 100 req/jour si on réduit à 2 matchs par check.
    # Je recommande de passer à 1 check par heure pour être large.
    schedule.every(1).hours.do(check_value_bets) 
    while True:
        schedule.run_pending()
        time.sleep(1)

threading.Thread(target=run_scheduler, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
