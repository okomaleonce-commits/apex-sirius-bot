import requests
import telebot
import time
import schedule
import os
import threading
import json
from flask import Flask, render_template_string
from datetime import datetime, timedelta

print("🚀 APEX-SIRIUS vFIX-TIME + PERSISTANCE", flush=True)

# ====================== CONFIG ======================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
API_KEY = os.environ.get("API_KEY")

if not all([BOT_TOKEN, CHAT_ID, API_KEY]):
    print("❌ Variables manquantes", flush=True)
    raise SystemExit("Config error")

bot = telebot.TeleBot(BOT_TOKEN)
BASE_URL = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": API_KEY}

# ====================== PERSISTANCE ALERTES ======================
# Pour éviter les doublons après restart Render
ALERTS_FILE = "/tmp/sent_alerts.json"  # /tmp est persistant pendant la vie du container
sent_alerts = set()

def load_alerts():
    global sent_alerts
    try:
        if os.path.exists(ALERTS_FILE):
            with open(ALERTS_FILE, 'r') as f:
                data = json.load(f)
                sent_alerts = set(data.get('alerts', []))
                print(f"💾 {len(sent_alerts)} alertes chargées depuis fichier", flush=True)
    except Exception as e:
        print(f"⚠️ Erreur chargement alerts: {e}", flush=True)
        sent_alerts = set()

def save_alerts():
    try:
        with open(ALERTS_FILE, 'w') as f:
            json.dump({'alerts': list(sent_alerts)}, f)
    except Exception as e:
        print(f"⚠️ Erreur sauvegarde alerts: {e}", flush=True)

# Charger au démarrage
load_alerts()

value_bets_history = []
debug_structure_logged = False

# ====================== API ======================
def safe_api_call(url):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        if resp.status_code == 200:
            return resp.json()
        elif resp.status_code == 429:
            print("🛑 QUOTA 429", flush=True)
            time.sleep(5)
        else:
            print(f"⚠️ API {resp.status_code}", flush=True)
    except Exception as e:
        print(f"⚠️ API Error: {e}", flush=True)
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

# ====================== VALUE BET ======================
def calcul_value_bet(odds_data, prediction):
    global debug_structure_logged
    if not odds_data or not prediction:
        return None

    if not debug_structure_logged:
        print(f"🔍 Structure: {prediction.get('predictions',{}).get('percent')}", flush=True)
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

                if name == "Match Winner":
                    for v in vals:
                        odd = float(v['odd'])
                        if odd < 1.40: 
                            continue
                        implied = 1 / odd
                        
                        if v['value'] == 'Home' and (pred_home - implied) > edge:
                            values.append(f"🏠 HOME | Cote {odd} | Edge +{(pred_home-implied)*100:.1f}%")
                        elif v['value'] == 'Draw' and (pred_draw - implied) > edge:
                            values.append(f"⚖️ DRAW | Cote {odd} | Edge +{(pred_draw-implied)*100:.1f}%")
                        elif v['value'] == 'Away' and (pred_away - implied) > edge:
                            values.append(f"🏃 AWAY | Cote {odd} | Edge +{(pred_away-implied)*100:.1f}%")

                if name == "Goals Over/Under":
                    for v in vals:
                        if v['value'] == 'Over 2.5':
                            odd = float(v['odd'])
                            implied = 1 / odd
                            if (0.58 - implied) > edge:
                                values.append(f"📈 OVER 2.5 | Cote {odd} | Edge +{(0.58-implied)*100:.1f}%")
    except Exception as e:
        print(f"❌ Erreur calcul: {e}", flush=True)
    
    return "\n".join(values) if values else None

# ====================== NOTIFICATION ======================
def envoyer_notification(message, fixture_id, country, league, date_time):
    alert_key = f"{fixture_id}_{hash(message)}"
    if alert_key in sent_alerts:
        return
    
    sent_alerts.add(alert_key)
    save_alerts()  # Persister immédiatement

    full_msg = f"""🚨 APEX-SIRIUS VALUE BET

🌍 {country} | 🏆 {league}
🕒 {date_time}

{message}"""

    try:
        bot.send_message(CHAT_ID, full_msg, parse_mode='HTML')
        print(f"✅ Notif envoyée: {fixture_id}", flush=True)
        value_bets_history.append({
            "time": datetime.now().strftime("%H:%M"), 
            "message": full_msg,
            "fixture": fixture_id
        })
        if len(value_bets_history) > 100:
            value_bets_history.pop(0)
    except Exception as e:
        print(f"❌ Telegram error: {e}", flush=True)

# ====================== CHECK CORRIGÉ ======================
def check_value_bets():
    print(f"\n⏰ SCAN {datetime.now().strftime('%H:%M:%S')}", flush=True)
    fixtures = get_fixtures()
    print(f"📊 {len(fixtures)} matchs aujourd'hui", flush=True)

    now = datetime.utcnow()  # API renvoie UTC
    count_analyzed = 0
    count_value = 0
    
    # Filtrer d'abord les matchs pertinents (à venir dans les 24h, pas terminés)
    valid_fixtures = []
    for f in fixtures:
        status = f['fixture']['status']['short']
        
        # Skip matchs terminés
        if status in ['FT', 'AET', 'PEN', 'CANC', 'PST', 'ABD']:
            continue
            
        try:
            # Parser la date API (format ISO avec Z)
            date_str = f['fixture']['date']
            if date_str.endswith('Z'):
                date_str = date_str[:-1] + '+00:00'
            match_date = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
            
            # Garder seulement si dans les prochaines 24h
            time_until = match_date - now
            hours_until = time_until.total_seconds() / 3600
            
            if -1 <= hours_until <= 24:  # En cours (-1h) ou dans les prochaines 24h
                valid_fixtures.append((f, hours_until))
                
        except Exception as e:
            continue
    
    print(f"🔍 {len(valid_fixtures)} matchs valides (dans 24h)", flush=True)
    
    # Limiter à 30 pour éviter timeout Render
    for fixture, hours_until in sorted(valid_fixtures, key=lambda x: x[1])[:30]:
        try:
            status = fixture['fixture']['status']['short']
            country = fixture.get('league', {}).get('country', 'Inconnu')
            league_name = fixture.get('league', {}).get('name', 'Inconnu')
            date_time = fixture['fixture']['date'][:16].replace('T', ' ')
            home = fixture['teams']['home']['name']
            away = fixture['teams']['away']['name']
            fid = fixture['fixture']['id']

            count_analyzed += 1
            time_label = "LIVE" if status in ['1H', '2H', 'HT'] else f"Dans {hours_until:.1f}h"
            print(f"✅ {time_label}: {league_name} - {home} vs {away}", flush=True)

            pred = get_predictions(fid)
            if not pred:
                continue
                
            odds = get_odds(fid)
            if not odds:
                continue
            
            value_msg = calcul_value_bet(odds, pred)
            if value_msg:
                count_value += 1
                msg = f"{home} vs {away}\n\n{value_msg}"
                envoyer_notification(msg, fid, country, league_name, date_time)
                
        except Exception as e:
            print(f"❌ Erreur match: {e}", flush=True)
            continue

    print(f"📈 {count_analyzed} analysés | {count_value} value bets\n", flush=True)

# ====================== FLASK ======================
app = Flask(__name__)

@app.route('/')
def home():
    return """
    <h1>🤖 APEX-SIRIUS Bot</h1>
    <p>✅ Running - vFIX-TIME</p>
    <p><a href="/test">🚀 Forcer Scan</a> | <a href="/dashboard">Dashboard</a></p>
    """

@app.route('/ping')
def ping():
    return "pong", 200

@app.route('/test')
def test():
    # Lancer dans un thread séparé pour ne pas bloquer la réponse HTTP
    def run_scan():
        check_value_bets()
    
    thread = threading.Thread(target=run_scan)
    thread.start()
    return "✅ Scan lancé (vérifiez les logs)", 200

@app.route('/dashboard')
def dashboard():
    html = """
    <html>
    <head>
        <title>APEX Dashboard</title>
        <meta http-equiv="refresh" content="60">
        <style>
            body{font-family:Arial;background:#111;color:#eee;padding:20px;}
            .alert{background:#1e293b;padding:15px;margin:10px 0;border-radius:8px;border-left:4px solid #38bdf8;}
            .live{border-left-color:#ef4444;}
        </style>
    </head>
    <body>
        <h1>🚀 APEX-SIRIUS Dashboard</h1>
        <p>Refresh: 60s | <a href="/test" style="color:#38bdf8;">🚀 Forcer Scan</a></p>
        {% for bet in history %}
        <div class="alert {% if 'LIVE' in bet.message %}live{% endif %}">
            <b>{{ bet.time }}</b>
            <pre style="white-space:pre-wrap;">{{ bet.message }}</pre>
        </div>
        {% endfor %}
    </body>
    </html>
    """
    return render_template_string(html, history=value_bets_history[::-1])

# ====================== SCHEDULER ======================
def run_scheduler():
    print("🗓️ Scheduler démarré (30min)", flush=True)
    # Premier scan après 10s
    time.sleep(10)
    check_value_bets()
    
    schedule.every(30).minutes.do(check_value_bets)
    while True:
        schedule.run_pending()
        time.sleep(1)

# Démarrer le scheduler en arrière-plan
threading.Thread(target=run_scheduler, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    print(f"🌐 Flask sur port {port}", flush=True)
    app.run(host='0.0.0.0', port=port)
        
