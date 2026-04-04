import requests
import telebot
import time
import schedule
import os
import sys
import threading
from flask import Flask, render_template_string
from datetime import datetime, timezone, timedelta

print("🚀 APEX-SIRIUS vPROD-FINALE - CORRECTION TIMEZONE + TRI", flush=True)

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

# ====================== API ======================
def safe_api_call(url):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=12)
        if resp.status_code == 200:
            return resp.json()
        elif resp.status_code == 429:
            print("🛑 QUOTA ATTEINT (429) - Arrêt des appels", flush=True)
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
    # Note: structure injury ne contient pas fixture.home.id directement, mais on simplifie ici
    return f"🩹 {len(injuries)} joueurs blessés listés"

# ====================== CALCUL VALUE BET ======================
def calcul_value_bet(odds_data, prediction, fixture):
    global debug_structure_logged
    if not odds_data or not prediction:
        return None

    values = []
    try:
        preds = prediction.get('predictions', {})
        percent = preds.get('percent', {})

        # Extraction des probabilités
        def get_pct(key):
            val = percent.get(key, '0')
            return float(str(val).replace('%', '')) / 100

        pred_home = get_pct('home')
        pred_draw = get_pct('draw')
        pred_away = get_pct('away')

        if pred_home + pred_draw + pred_away == 0:
            return None

        edge = 0.02 # Seuil minimum de value (2%)

        # Parcours des cotes
        for bm in odds_data[0].get('bookmakers', []):
            if bm['name'].lower() not in ['pinnacle', 'betway', 'bet365']:
                continue
            for bet_group in bm['bets']:
                name = bet_group['name']
                vals = bet_group['values']

                if name == "Match Winner":
                    for v in vals:
                        odd = float(v['odd'])
                        if odd < 1.40: continue
                        implied = 1 / odd
                        
                        if v['value'] == 'Home' and (pred_home - implied) > edge:
                            values.append(f"🏠 HOME VALUE : {pred_home*100:.1f}% vs {odd} (edge +{((pred_home-implied)*100):.1f}%)")
                        elif v['value'] == 'Draw' and (pred_draw - implied) > edge:
                            values.append(f"⚖️ DRAW VALUE : {pred_draw*100:.1f}% vs {odd}")
                        elif v['value'] == 'Away' and (pred_away - implied) > edge:
                            values.append(f"🏃 AWAY VALUE : {pred_away*100:.1f}% vs {odd}")

                # Ajoute ici d'autres marchés (Over, BTTS) si nécessaire...
                
    except Exception as e:
        print(f"❌ Erreur calcul: {e}", flush=True)

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
        if len(value_bets_history) > 30: value_bets_history.pop(0)
    except Exception as e:
        print(f"❌ Erreur Telegram: {e}", flush=True)

# ====================== CHECK ======================
def check_value_bets():
    print(f"\n⏰ Check lancé à {datetime.now().strftime('%H:%M:%S')}", flush=True)
    fixtures = get_fixtures()
    if not fixtures:
        print("Aucun match trouvé.", flush=True)
        return

    print(f"📊 {len(fixtures)} matchs chargés.", flush=True)

    # 1. Définition du temps actuel en UTC (obligatoire car API est en UTC)
    now = datetime.now(timezone.utc)
    
    candidates = []

    # 2. Filtrage et Tri
    for fixture in fixtures:
        status = fixture['fixture']['status']['short']
        
        # On ignore les matchs terminés
        if status in ['FT', 'AET', 'PEN', 'CANC', 'PST', 'ABD', 'AWD', 'WO']:
            continue

        try:
            # Parsing de la date (Aware DateTime)
            match_date = datetime.fromisoformat(fixture['fixture']['date'].replace('Z', '+00:00'))
            
            # On garde les matchs qui commencent dans moins de 4h OU qui ont commencé il y a moins de 2h (Live)
            # Tu peux ajuster ces valeurs
            time_until_start = match_date - now
            
            # Si le match commence dans plus de 5h, on ignore (pour se concentrer sur le proche)
            if time_until_start > timedelta(hours=5):
                continue
            
            # Si le match est terminé ou trop vieux (commencé il y a + de 3h), on ignore
            if match_date < (now - timedelta(hours=3)):
                continue

            candidates.append({
                'fixture': fixture,
                'date': match_date
            })

        except Exception as e:
            # Debug si jamais une date est mal formatée
            # print(f"Erreur date parsing: {e}") 
            continue

    # Tri par date (les plus proches d'abord)
    candidates.sort(key=lambda x: x['date'])

    print(f"🗓️ {len(candidates)} matchs éligibles trouvés.", flush=True)

    count_analyzed = 0
    count_value = 0
    
    # 3. Analyse (Limité à 20 pour le QUOTA GRATUIT)
    # 20 matchs = 40 appels API. Il te reste de la marge pour la journée.
    LIMIT = 20 
    
    for item in candidates[:LIMIT]:
        fixture = item['fixture']
        fid = fixture['fixture']['id']
        
        country = fixture.get('league', {}).get('country', 'N/A')
        league_name = fixture.get('league', {}).get('name', 'N/A')
        date_time = fixture['fixture']['date'][:16].replace('T', ' ')
        home = fixture['teams']['home']['name']
        away = fixture['teams']['away']['name']

        count_analyzed += 1
        # Log minimal pour suivre
        print(f"-> Analyse: {home} vs {away}", flush=True)

        pred = get_predictions(fid)
        odds = get_odds(fid)

        if pred and odds:
            value_msg = calcul_value_bet(odds, pred, fid)
            if value_msg:
                count_value += 1
                injuries = get_injuries(fid) # Appelé seulement si value bet trouvé
                msg = f"{home} vs {away}\n\n{value_msg}\n\n{injuries}"
                envoyer_notification(msg, fid, country, league_name, date_time)
        
        # Pause pour ne pas spammer l'API trop vite
        time.sleep(0.5)

    print(f"✅ Terminé: {count_analyzed} analysés | {count_value} alertes.", flush=True)

# ====================== FLASK ======================
app = Flask(__name__)

@app.route('/')
def home():
    return "🤖 APEX-SIRIUS RUNNING"

@app.route('/test')
def test():
    check_value_bets()
    return "Scan lancé", 200

@app.route('/dashboard')
def dashboard():
    html = """
    <html><head><title>Dashboard</title>
    <meta http-equiv="refresh" content="30">
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
    schedule.every(30).minutes.do(check_value_bets)
    while True:
        schedule.run_pending()
        time.sleep(1)

threading.Thread(target=run_scheduler, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
