import requests
import telebot
import time
import schedule
import os
import threading
from flask import Flask, render_template_string
from datetime import datetime

print("🚀 APEX-SIRIUS vPROD-FINALE - 3% edge + toutes features", flush=True)

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

# ====================== LIGUES ======================
LEAGUE_KEYWORDS = [
    "premier", "championship", "la liga", "segunda", "bundesliga", "2. bundesliga",
    "serie a", "serie b", "ligue 1", "ligue 2", "eredivisie", "eerste", "primeira",
    "champions", "europa", "conference", "premiership", "pro league", "j1", "saudi",
    "russian", "egyptian", "maltese", "greek", "tunisian", "africa cup", "world cup",
    "friendlies", "jupiler", "allsvenskan", "ekstraklasa", "super league", "k league",
    "ligat", "süper lig", "veikkausliiga", "meistriliiga", "virsliga", "a lyga"
]

# ====================== API ======================
def safe_api_call(url):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=12)
        if resp.status_code == 200:
            return resp.json()
        elif resp.status_code == 429:
            print("🛑 QUOTA 429", flush=True)
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
    home_inj = len([p for p in injuries if p.get('team', {}).get('id') == p.get('fixture', {}).get('home', {}).get('id')])
    away_inj = len([p for p in injuries if p.get('team', {}).get('id') == p.get('fixture', {}).get('away', {}).get('id')])
    return f"🩹 Home: {home_inj} | Away: {away_inj}"

# ====================== CALCUL VALUE BET ======================
def calcul_value_bet(odds_data, prediction, fixture):
    global debug_structure_logged
    if not odds_data or not prediction:
        return None

    if not debug_structure_logged:
        print(f"🔍 STRUCTURE PREDICTION : {prediction.get('predictions')}", flush=True)
        debug_structure_logged = True

    values = []
    try:
        preds = prediction.get('predictions', {})
        percent = preds.get('percent', {})

        pred_home = float(str(percent.get('home', '0')).replace('%', '')) / 100
        pred_draw = float(str(percent.get('draw', '0')).replace('%', '')) / 100
        pred_away = float(str(percent.get('away', '0')).replace('%', '')) / 100

        edge = 0.03

        for bm in odds_data[0].get('bookmakers', []):
            if bm['name'].lower() not in ['pinnacle', 'betway', 'bet365']:
                continue
            for bet_group in bm['bets']:
                name = bet_group['name']
                vals = bet_group['values']

                # 1X2
                if name == "Match Winner":
                    for v in vals:
                        odd = float(v['odd'])
                        if odd < 1.40: continue
                        implied = 1 / odd
                        if v['value'] == 'Home' and (pred_home - implied) > edge:
                            values.append(f"🏠 HOME VALUE : {pred_home*100:.1f}% vs {odd} (edge +{(pred_home-implied)*100:.1f}%)")
                        elif v['value'] == 'Draw' and (pred_draw - implied) > edge:
                            values.append(f"⚖️ DRAW VALUE : {pred_draw*100:.1f}% vs {odd} (edge +{(pred_draw-implied)*100:.1f}%)")
                        elif v['value'] == 'Away' and (pred_away - implied) > edge:
                            values.append(f"🏃 AWAY VALUE : {pred_away*100:.1f}% vs {odd} (edge +{(pred_away-implied)*100:.1f}%)")

                # Over 2.5
                if name == "Goals Over/Under":
                    for v in vals:
                        if v['value'] == 'Over 2.5':
                            odd = float(v['odd'])
                            implied = 1 / odd
                            if (0.58 - implied) > edge:
                                values.append(f"🔥 OVER 2.5 : {odd} (edge +{(0.58-implied)*100:.1f}%)")

                # BTTS
                if name == "Both Teams To Score":
                    for v in vals:
                        if v['value'] == 'Yes':
                            odd = float(v['odd'])
                            implied = 1 / odd
                            if (0.55 - implied) > edge:
                                values.append(f"🔄 BTTS YES : {odd} (edge +{(0.55-implied)*100:.1f}%)")

                # Corners
                if name == "Corners Over/Under":
                    for v in vals:
                        if "Over 9.5" in v['value']:
                            odd = float(v['odd'])
                            implied = 1 / odd
                            if (0.52 - implied) > edge:
                                values.append(f"📐 OVER 9.5 CORNERS : {odd} (edge +{(0.52-implied)*100:.1f}%)")

                # Tirs cadrés
                if name == "Shots on Goal":
                    for v in vals:
                        if "Over 4.5" in v['value']:
                            odd = float(v['odd'])
                            implied = 1 / odd
                            if (0.50 - implied) > edge:
                                values.append(f"🎯 OVER 4.5 SHOTS ON TARGET : {odd} (edge +{(0.50-implied)*100:.1f}%)")
    except Exception as e:
        print(f"❌ Erreur calcul_value_bet : {e}", flush=True)

    return "\n".join(values) if values else None

# ====================== NOTIFICATION ======================
def envoyer_notification(message, fixture_id, country, league, date_time):
    alert_key = f"{fixture_id}_{hash(message)}"
    if alert_key in sent_alerts:
        return
    sent_alerts.add(alert_key)

    full_msg = f"""🚨 APEX-SIRIUS VALUE BET

🌍 {country} | 🏆 {league}
🕒 {date_time}

{message}"""

    try:
        bot.send_message(CHAT_ID, full_msg)
        print(f"✅ Telegram envoyé pour {fixture_id}", flush=True)
        value_bets_history.append({"time": datetime.now().strftime("%H:%M"), "message": full_msg})
    except Exception as e:
        print(f"❌ Erreur Telegram: {e}", flush=True)

# ====================== CHECK ======================
def check_value_bets():
    print(f"\n⏰ Check lancé à {datetime.now().strftime('%H:%M:%S')}", flush=True)
    fixtures = get_fixtures()
    print(f"📊 {len(fixtures)} matchs trouvés aujourd'hui.", flush=True)

    count_analyzed = 0
    count_value = 0

    for fixture in fixtures[:20]:
        status = fixture['fixture']['status']['short']
        if status in ['FT', 'AET', 'PEN', 'CANC', 'PST', 'ABD']:
            continue  # uniquement pré-match + live

        league_name = fixture.get('league', {}).get('name', '')
        league_lower = league_name.lower()
        if not any(kw in league_lower for kw in LEAGUE_KEYWORDS):
            continue

        country = fixture.get('league', {}).get('country', 'Inconnu')
        date_time = fixture['fixture']['date'][:16].replace('T', ' ')
        home = fixture['teams']['home']['name']
        away = fixture['teams']['away']['name']

        count_analyzed += 1
        print(f"✅ Analyzing: {league_name} - {home} vs {away} ({status})", flush=True)

        pred = get_predictions(fixture['fixture']['id'])
        odds = get_odds(fixture['fixture']['id'])

        if pred and odds:
            value_msg = calcul_value_bet(odds, pred, fixture['fixture']['id'])
            if value_msg:
                count_value += 1
                injuries = get_injuries(fixture['fixture']['id'])
                msg = f"{home} vs {away}\n\n{value_msg}\n\n{injuries}"
                envoyer_notification(msg, fixture['fixture']['id'], country, league_name, date_time)

    print(f"🔍 Analyse terminée: {count_analyzed} analysés | {count_value} value bets trouvés\n", flush=True)

# ====================== FLASK ======================
app = Flask(__name__)

@app.route('/')
def home():
    return "🤖 APEX-SIRIUS Bot PROD FINALE Running 24/7"

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
    <body><h1>🚀 APEX-SIRIUS Dashboard PROD</h1>
    {% for bet in history %}<div><b>{{ bet.time }}</b><pre>{{ bet.message }}</pre></div><hr>{% endfor %}
    </body></html>
    """
    return render_template_string(html, history=value_bets_history[::-1])

def run_scheduler():
    print("🗓️ Scheduler démarré...", flush=True)
    time.sleep(5)
    check_value_bets()
    schedule.every(30).minutes.do(check_value_bets)
    while True:
        schedule.run_pending()
        time.sleep(1)

threading.Thread(target=run_scheduler, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)