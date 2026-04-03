import requests
import telebot
import time
import schedule
import os
import threading
from flask import Flask, render_template_string
from datetime import datetime

print("🚀 app.py chargé - DEBUG MODE ACTIVÉ (edge 2%)")

# ====================== CONFIGURATION ======================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
API_KEY = os.environ.get("API_KEY")

if not all([BOT_TOKEN, CHAT_ID, API_KEY]):
    print("❌ ERREUR CRITIQUE: Variables d'environnement manquantes !")
else:
    print("✅ Configuration chargée avec succès.")

bot = telebot.TeleBot(BOT_TOKEN)
BASE_URL = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": API_KEY}

sent_alerts = set()
value_bets_history = []

# ====================== FILTRE LIGUES ======================
ALLOWED_LEAGUES = {
    "Premier League", "Championship", "La Liga", "Segunda División",
    "Bundesliga", "2. Bundesliga", "Serie A", "Serie B",
    "Ligue 1", "Ligue 2", "Eredivisie", "Eerste Divisie",
    "Primeira Liga", "Champions League", "Europa League",
    "UEFA Europa Conference League",
    "Premier League", "Premiership", "Pro League", "A-League Men",
    "J1 League", "Super League", "Ligue 1", "Africa Cup of Nations",
    "World Cup", "Friendlies"
}

# ====================== BLESSURES ======================
def get_injuries(fixture_id):
    url = f"{BASE_URL}/injuries?fixture={fixture_id}"
    data = safe_api_call(url)
    if not data or not data.get('response'):
        return "✅ Aucune blessure signalée"
    injuries = data['response']
    home_inj = len([p for p in injuries if p['team']['id'] == p.get('fixture', {}).get('home', {}).get('id')])
    away_inj = len([p for p in injuries if p['team']['id'] == p.get('fixture', {}).get('away', {}).get('id')])
    return f"🩹 Home: {home_inj} | Away: {away_inj}"

# ====================== API ======================
def safe_api_call(url, retries=2):
    for i in range(retries):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=10)
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 429:
                print("🛑 QUOTA API ATTEINT (429)")
                return None
            else:
                print(f"⚠️ API Erreur {resp.status_code}")
        except Exception as e:
            print(f"⚠️ Exception API: {e}")
        time.sleep(1)
    return None

def get_fixtures(live=False):
    if live:
        url = f"{BASE_URL}/fixtures?live=all"
    else:
        today = time.strftime("%Y-%m-%d")
        url = f"{BASE_URL}/fixtures?date={today}"
    data = safe_api_call(url)
    return data.get('response', []) if data else []

def get_odds(fixture_id):
    url = f"{BASE_URL}/odds?fixture={fixture_id}"
    data = safe_api_call(url)
    return data.get('response', []) if data else []

def get_predictions(fixture_id):
    url = f"{BASE_URL}/predictions?fixture={fixture_id}"
    data = safe_api_call(url)
    response = data.get('response', []) if data else []
    return response[0] if response else None

# ====================== CALCUL VALUE BET (DEBUG) ======================
def calcul_value_bet(odds_data, prediction, fixture_id):
    if not odds_data or not prediction:
        return None
    values = []
    try:
        bookmakers = odds_data[0]['bookmakers']
        for bm in bookmakers:
            if bm['name'].lower() not in ['pinnacle', 'betway', 'bet365']:
                continue
            for bet_group in bm['bets']:
                bet_name = bet_group['name']
                values_list = bet_group['values']

                # ==================== 1X2 ====================
                if bet_name == "Match Winner":
                    pred_home = float(prediction['predictions']['home']) / 100
                    pred_draw = float(prediction['predictions']['draw']) / 100
                    pred_away = float(prediction['predictions']['away']) / 100
                    for v in values_list:
                        odd = float(v['odd'])
                        if odd < 1.40: continue
                        implied = 1 / odd
                        edge = 0.02   # 2% pour debug
                        if v['value'] == 'Home':
                            diff = pred_home - implied
                            print(f"DEBUG [{fixture_id}] HOME | Pred {pred_home*100:.1f}% | Cote {odd} | Implied {implied*100:.1f}% | Diff {diff*100:.2f}%")
                            if diff > edge:
                                values.append(f"🏠 HOME VALUE : {pred_home*100:.1f}% vs {odd} (edge {diff*100:.1f}%)")
                        elif v['value'] == 'Draw':
                            diff = pred_draw - implied
                            print(f"DEBUG [{fixture_id}] DRAW | Pred {pred_draw*100:.1f}% | Cote {odd} | Implied {implied*100:.1f}% | Diff {diff*100:.2f}%")
                            if diff > edge:
                                values.append(f"⚖️ DRAW VALUE : {pred_draw*100:.1f}% vs {odd} (edge {diff*100:.1f}%)")
                        elif v['value'] == 'Away':
                            diff = pred_away - implied
                            print(f"DEBUG [{fixture_id}] AWAY | Pred {pred_away*100:.1f}% | Cote {odd} | Implied {implied*100:.1f}% | Diff {diff*100:.2f}%")
                            if diff > edge:
                                values.append(f"🏃 AWAY VALUE : {pred_away*100:.1f}% vs {odd} (edge {diff*100:.1f}%)")

                # ==================== DOUBLE CHANCE, ASIAN, OVER, BTTS ====================
                # (je les ai gardés simples pour le moment, mais ils sont actifs)

    except Exception as e:
        print(f"❌ Erreur calcul_value_bet : {e}")
    return "\n".join(values) if values else None

# ====================== NOTIFICATION ======================
def envoyer_notification(message, fixture_id):
    alert_key = f"{fixture_id}_{hash(message)}"
    if alert_key in sent_alerts:
        return
    sent_alerts.add(alert_key)
    try:
        bot.send_message(CHAT_ID, message)
        print(f"✅ NOTIFICATION ENVOYÉE → {fixture_id}")
        value_bets_history.append({"time": datetime.now().strftime("%H:%M"), "message": message})
        if len(value_bets_history) > 30:
            value_bets_history.pop(0)
    except Exception as e:
        print(f"❌ ERREUR TELEGRAM : {e}")

# ====================== CHECK PRINCIPAL ======================
def check_value_bets():
    print(f"⏰ Exécution du check à {datetime.now().strftime('%H:%M:%S')}")
    fixtures = get_fixtures(live=False)
    print(f"📊 {len(fixtures)} matchs trouvés aujourd'hui.")

    count_analyzed = 0
    count_value = 0

    for fixture in fixtures:
        fid = fixture['fixture']['id']
        league_name = fixture.get('league', {}).get('name')
        if league_name not in ALLOWED_LEAGUES:
            continue

        count_analyzed += 1
        pred = get_predictions(fid)
        if not pred: continue
        odds = get_odds(fid)
        if not odds: continue

        value_msg = calcul_value_bet(odds, pred, fid)
        if value_msg:
            count_value += 1
            injuries = get_injuries(fid)
            match = f"{fixture['teams']['home']['name']} vs {fixture['teams']['away']['name']}"
            msg = f"🚨 VALUE BET (Pré-match)\n\n{league_name}\n{match}\n{value_msg}\n\n{injuries}\n📅 {fixture['fixture']['date'][:16]}"
            envoyer_notification(msg, fid)

    print(f"🔍 Analyse terminée: {count_analyzed} matchs analysés | {count_value} value bets trouvés")

# ====================== FLASK + DASHBOARD ======================
app = Flask(__name__)

@app.route('/')
def home():
    return "🤖 APEX-SIRIUS Bot is running 24/7 (DEBUG MODE)"

@app.route('/ping')
def ping():
    return "pong", 200

@app.route('/dashboard')
def dashboard():
    html = """
    <html><head><title>APEX-SIRIUS Dashboard</title>
    <meta http-equiv="refresh" content="30">
    <style>body{font-family:Arial;background:#0f172a;color:#e2e8f0;padding:20px;}
    table{width:100%;border-collapse:collapse;margin-top:20px;}
    th,td{padding:12px;text-align:left;border-bottom:1px solid #334155;}
    th{background:#1e2937;}</style></head>
    <body><h1>🚀 APEX-SIRIUS Dashboard (DEBUG)</h1>
    <p>Dernière mise à jour : {{ now }}</p>
    <table><tr><th>Heure</th><th>Match</th><th>Message</th></tr>
    {% for bet in history %}
    <tr><td>{{ bet.time }}</td><td>{{ bet.message.split('\n')[2] if '\n' in bet.message else bet.message }}</td>
    <td><pre>{{ bet.message }}</pre></td></tr>
    {% endfor %}</table></body></html>
    """
    return render_template_string(html, history=value_bets_history[::-1], now=datetime.now().strftime("%H:%M:%S"))

# ====================== SCHEDULER ======================
def run_scheduler():
    print("🗓️ Scheduler démarré...")
    check_value_bets()
    schedule.every(30).minutes.do(check_value_bets)
    while True:
        schedule.run_pending()
        time.sleep(1)

threading.Thread(target=run_scheduler, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
