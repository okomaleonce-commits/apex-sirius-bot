import requests
import telebot
import time
import schedule
import os
import threading
from flask import Flask, render_template_string
from datetime import datetime

print("🚀 APEX-SIRIUS vFINAL - DEBUG + CORRECTION PROBABILITÉS", flush=True)

# ====================== CONFIGURATION ======================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
API_KEY = os.environ.get("API_KEY")

if not all([BOT_TOKEN, CHAT_ID, API_KEY]):
    print("❌ ERREUR CRITIQUE: Variables d'environnement manquantes !", flush=True)
else:
    print("✅ Configuration chargée avec succès.", flush=True)

bot = telebot.TeleBot(BOT_TOKEN)
BASE_URL = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": API_KEY}

sent_alerts = set()
value_bets_history = []
debug_structure_logged = False

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
                print("🛑 QUOTA API ATTEINT (429)", flush=True)
                return None
            else:
                print(f"⚠️ API Erreur {resp.status_code}", flush=True)
        except Exception as e:
            print(f"⚠️ Exception API: {e}", flush=True)
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

# ====================== CALCUL VALUE BET (CORRIGÉ) ======================
def calcul_value_bet(odds_data, prediction, fixture_id):
    global debug_structure_logged
    if not odds_data or not prediction:
        return None

    if not debug_structure_logged:
        print(f"🔍 STRUCTURE PREDICTION BRUTE (Match {fixture_id}): {prediction.get('predictions')}", flush=True)
        debug_structure_logged = True

    values = []
    try:
        preds = prediction.get('predictions', {})
        pred_home = float(preds.get('home_team_win_probability', 0)) / 100
        pred_draw = float(preds.get('draw_probability', 0)) / 100
        pred_away = float(preds.get('away_team_win_probability', 0)) / 100

        edge = 0.02  # 2% (tu peux mettre 0.0 pour tester)

        bookmakers = odds_data[0].get('bookmakers', [])
        for bm in bookmakers:
            if bm['name'].lower() not in ['pinnacle', 'betway', 'bet365']:
                continue
            for bet_group in bm['bets']:
                bet_name = bet_group['name']
                values_list = bet_group['values']

                # 1X2
                if bet_name == "Match Winner":
                    for v in values_list:
                        odd = float(v['odd'])
                        if odd < 1.40: continue
                        implied = 1 / odd

                        if v['value'] == 'Home':
                            edge_calc = pred_home - implied
                            if edge_calc > edge:
                                values.append(f"🏠 HOME VALUE : {pred_home*100:.1f}% vs {odd} (edge {edge_calc*100:.1f}%)")
                            print(f"[DEBUG] HOME | odd={odd:.2f} | implied={implied*100:.1f}% | pred={pred_home*100:.1f}% | edge={edge_calc*100:.1f}%", flush=True)

                        elif v['value'] == 'Draw':
                            edge_calc = pred_draw - implied
                            if edge_calc > edge:
                                values.append(f"⚖️ DRAW VALUE : {pred_draw*100:.1f}% vs {odd} (edge {edge_calc*100:.1f}%)")
                            print(f"[DEBUG] DRAW | odd={odd:.2f} | implied={implied*100:.1f}% | pred={pred_draw*100:.1f}% | edge={edge_calc*100:.1f}%", flush=True)

                        elif v['value'] == 'Away':
                            edge_calc = pred_away - implied
                            if edge_calc > edge:
                                values.append(f"🏃 AWAY VALUE : {pred_away*100:.1f}% vs {odd} (edge {edge_calc*100:.1f}%)")
                            print(f"[DEBUG] AWAY | odd={odd:.2f} | implied={implied*100:.1f}% | pred={pred_away*100:.1f}% | edge={edge_calc*100:.1f}%", flush=True)

                # Double Chance, Asian Handicap, Over 2.5, BTTS (à compléter si besoin)
                # ... (tu peux garder le code des autres marchés de la version précédente)

    except Exception as e:
        print(f"❌ Erreur calcul_value_bet : {e}", flush=True)

    return "\n".join(values) if values else None

# ====================== NOTIFICATION ======================
def envoyer_notification(message, fixture_id):
    alert_key = f"{fixture_id}_{hash(message)}"
    if alert_key in sent_alerts:
        return
    sent_alerts.add(alert_key)
    try:
        bot.send_message(CHAT_ID, message)
        print(f"✅ Notification Telegram envoyée pour {fixture_id}", flush=True)
        value_bets_history.append({"time": datetime.now().strftime("%H:%M"), "message": message})
        if len(value_bets_history) > 30:
            value_bets_history.pop(0)
    except Exception as e:
        print(f"❌ Erreur Telegram : {e}", flush=True)

# ====================== CHECK ======================
def check_value_bets():
    print(f"⏰ Exécution du check à {datetime.now().strftime('%H:%M:%S')}", flush=True)
    fixtures = get_fixtures()
    print(f"📊 {len(fixtures)} matchs trouvés aujourd'hui.", flush=True)

    count_analyzed = 0
    count_value = 0

    # Limite à 15 matchs les plus proches pour économiser le quota
    for fixture in fixtures[:15]:
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

    print(f"🔍 Analyse terminée: {count_analyzed} matchs analysés | {count_value} value bets trouvés", flush=True)

# ====================== FLASK + DASHBOARD ======================
app = Flask(__name__)

@app.route('/')
def home():
    return "🤖 APEX-SIRIUS Bot is running 24/7 !"

@app.route('/ping')
def ping():
    return "pong", 200

@app.route('/test')
def test():
    print("🚨 SCAN MANUEL DEMANDÉ VIA /test", flush=True)
    check_value_bets()
    return "✅ Scan manuel lancé ! Regarde les logs Render.", 200

@app.route('/dashboard')
def dashboard():
    html = """
    <html><head><title>APEX-SIRIUS Dashboard</title>
    <meta http-equiv="refresh" content="30">
    <style>body{font-family:Arial;background:#0f172a;color:#e2e8f0;padding:20px;}</style>
    </head><body><h1>🚀 APEX-SIRIUS Dashboard</h1>
    <p>Dernière mise à jour : {{ now }}</p>
    <table style="width:100%;border-collapse:collapse;">
    <tr><th>Heure</th><th>Message</th></tr>
    {% for bet in history %}
    <tr><td>{{ bet.time }}</td><td><pre>{{ bet.message }}</pre></td></tr>
    {% endfor %}</table></body></html>
    """
    return render_template_string(html, history=value_bets_history[::-1], now=datetime.now().strftime("%H:%M:%S"))

# ====================== SCHEDULER ======================
def run_scheduler():
    print("🗓️ Scheduler démarré...", flush=True)
    check_value_bets()  # Premier check immédiat
    schedule.every(30).minutes.do(check_value_bets)
    while True:
        schedule.run_pending()
        time.sleep(1)

threading.Thread(target=run_scheduler, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)