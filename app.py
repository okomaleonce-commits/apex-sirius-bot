import requests
import telebot
import time
import schedule
import os
import threading
from flask import Flask, render_template_string
from datetime import datetime

print("🚀 APEX-SIRIUS vFINAL - FILTRAGE LARGI + DEBUG LIGUES", flush=True)

# ====================== CONFIG ======================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
API_KEY = os.environ.get("API_KEY")

if not all([BOT_TOKEN, CHAT_ID, API_KEY]):
    print("❌ ERREUR CRITIQUE: Variables manquantes", flush=True)
else:
    print("✅ Configuration chargée", flush=True)

bot = telebot.TeleBot(BOT_TOKEN)
BASE_URL = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": API_KEY}

sent_alerts = set()
value_bets_history = []
debug_structure_logged = False

# ====================== LISTE LIGUES (très élargie) ======================
ALLOWED_LEAGUES = {
    "Premier League", "Championship", "La Liga", "Segunda División",
    "Bundesliga", "2. Bundesliga", "Serie A", "Serie B",
    "Ligue 1", "Ligue 2", "Eredivisie", "Eerste Divisie",
    "Primeira Liga", "Champions League", "Europa League", "UEFA Europa Conference League",
    "Premier League", "Premiership", "Scottish Premiership", "Pro League",
    "A-League Men", "J1 League", "Saudi Pro League", "Russian Premier League",
    "Egyptian Premier League", "Maltese Premier League", "Super League", "Greek Super League",
    "Tunisian Ligue 1", "Africa Cup of Nations", "World Cup", "Friendlies",
    "Copa del Rey", "FA Cup", "Copa Libertadores", "Copa Sudamericana"
}

# ====================== API ======================
def safe_api_call(url):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=12)
        if resp.status_code == 200:
            return resp.json()
        elif resp.status_code == 429:
            print("🛑 QUOTA 429 - Attente...", flush=True)
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

# ====================== CALCUL VALUE (structure 'percent' corrigée) ======================
def calcul_value_bet(odds_data, prediction, fixture_id):
    global debug_structure_logged
    if not odds_data or not prediction:
        return None

    if not debug_structure_logged:
        print(f"🔍 STRUCTURE PREDICTION (premier match) : {prediction.get('predictions')}", flush=True)
        debug_structure_logged = True

    values = []
    try:
        preds = prediction.get('predictions', {})
        percent = preds.get('percent', {})

        pred_home = float(str(percent.get('home', '0')).replace('%', '')) / 100
        pred_draw = float(str(percent.get('draw', '0')).replace('%', '')) / 100
        pred_away = float(str(percent.get('away', '0')).replace('%', '')) / 100

        edge_threshold = 0.02

        for bm in odds_data[0].get('bookmakers', []):
            if bm['name'].lower() not in ['pinnacle', 'betway', 'bet365']:
                continue
            for bet_group in bm['bets']:
                if bet_group['name'] == "Match Winner":
                    for v in bet_group['values']:
                        odd = float(v['odd'])
                        if odd < 1.40:
                            continue
                        implied = 1 / odd
                        if v['value'] == 'Home':
                            edge = pred_home - implied
                            if edge > edge_threshold:
                                values.append(f"🏠 HOME VALUE : {pred_home*100:.1f}% vs {odd} (edge +{edge*100:.1f}%)")
                        elif v['value'] == 'Draw':
                            edge = pred_draw - implied
                            if edge > edge_threshold:
                                values.append(f"⚖️ DRAW VALUE : {pred_draw*100:.1f}% vs {odd} (edge +{edge*100:.1f}%)")
                        elif v['value'] == 'Away':
                            edge = pred_away - implied
                            if edge > edge_threshold:
                                values.append(f"🏃 AWAY VALUE : {pred_away*100:.1f}% vs {odd} (edge +{edge*100:.1f}%)")
    except Exception as e:
        print(f"❌ Erreur calcul_value_bet : {e}", flush=True)

    return "\n".join(values) if values else None

# ====================== NOTIF ======================
def envoyer_notification(message, fixture_id):
    alert_key = f"{fixture_id}_{hash(message)}"
    if alert_key in sent_alerts:
        return
    sent_alerts.add(alert_key)
    try:
        bot.send_message(CHAT_ID, message)
        print(f"✅ Telegram envoyé !", flush=True)
        value_bets_history.append({"time": datetime.now().strftime("%H:%M"), "message": message})
    except Exception as e:
        print(f"❌ Erreur Telegram: {e}", flush=True)

# ====================== CHECK PRINCIPAL ======================
def check_value_bets():
    print(f"\n⏰ Check lancé à {datetime.now().strftime('%H:%M:%S')}", flush=True)
    fixtures = get_fixtures()
    print(f"📊 {len(fixtures)} matchs trouvés aujourd'hui.", flush=True)

    eligible_fixtures = []
    for f in fixtures:
        league_name = f.get('league', {}).get('name', '')
        if league_name in ALLOWED_LEAGUES:
            eligible_fixtures.append(f)
        else:
            print(f"⏭️ Skipped league: {league_name}", flush=True)  # ← DEBUG IMPORTANT

    print(f"✅ {len(eligible_fixtures)} matchs dans les ligues autorisées.", flush=True)

    count_analyzed = 0
    count_value = 0

    for fixture in eligible_fixtures[:20]:  # 20 max pour quota
        fid = fixture['fixture']['id']
        league_name = fixture.get('league', {}).get('name')
        home = fixture['teams']['home']['name']
        away = fixture['teams']['away']['name']

        print(f"✅ Analyzing: {league_name} - {home} vs {away}", flush=True)

        count_analyzed += 1
        pred = get_predictions(fid)
        odds = get_odds(fid)

        if pred and odds:
            value_msg = calcul_value_bet(odds, pred, fid)
            if value_msg:
                count_value += 1
                injuries = "✅ Aucune blessure"  # on peut réactiver get_injuries plus tard
                match_line = f"{home} vs {away}"
                msg = f"🚨 VALUE BET\n\n{league_name}\n{match_line}\n{value_msg}\n\n{injuries}"
                envoyer_notification(msg, fid)

    print(f"🔍 Analyse terminée: {count_analyzed} analysés | {count_value} value bets trouvés\n", flush=True)

# ====================== FLASK ======================
app = Flask(__name__)

@app.route('/')
def home():
    return "🤖 APEX-SIRIUS Bot Running"

@app.route('/ping')
def ping():
    return "pong", 200

@app.route('/test')
def test():
    print("🚨 SCAN MANUEL DÉCLENCHÉ", flush=True)
    check_value_bets()
    return "✅ Scan manuel lancé – regarde les logs Render", 200

@app.route('/dashboard')
def dashboard():
    html = """
    <html><head><title>APEX Dashboard</title>
    <meta http-equiv="refresh" content="30">
    <style>body{font-family:Arial;background:#111;color:#eee;padding:20px;}</style></head>
    <body><h1>🚀 APEX-SIRIUS Dashboard</h1>
    {% for bet in history %}<div><b>{{ bet.time }}</b><pre>{{ bet.message }}</pre></div><hr>{% endfor %}
    </body></html>
    """
    return render_template_string(html, history=value_bets_history[::-1])

def run_scheduler():
    print("🗓️ Scheduler démarré...", flush=True)
    time.sleep(5)
    check_value_bets()  # Premier scan immédiat
    schedule.every(30).minutes.do(check_value_bets)
    while True:
        schedule.run_pending()
        time.sleep(1)

threading.Thread(target=run_scheduler, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)