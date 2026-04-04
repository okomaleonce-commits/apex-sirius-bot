import requests
import telebot
import time
import os
import threading
from flask import Flask, render_template_string
from datetime import datetime, timezone, timedelta

print("🚀 APEX-SIRIUS vSTABLE - TIME FILTER FIX", flush=True)

# ====================== CONFIG ======================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
API_KEY = os.environ.get("API_KEY")

if not all([BOT_TOKEN, CHAT_ID, API_KEY]):
    print("❌ Variables manquantes", flush=True)

bot = telebot.TeleBot(BOT_TOKEN)
BASE_URL = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": API_KEY}

sent_alerts = set()
value_bets_history = []

# ====================== API ======================
def safe_api_call(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        if r.status_code == 200:
            return r.json()
        elif r.status_code == 429:
            print("🛑 QUOTA API", flush=True)
        else:
            print(f"⚠️ API {r.status_code}", flush=True)
    except Exception as e:
        print(f"❌ API ERROR: {e}", flush=True)
    return None

def get_fixtures():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    url = f"{BASE_URL}/fixtures?date={today}"
    data = safe_api_call(url)
    return data.get('response', []) if data else []

def get_predictions(fid):
    url = f"{BASE_URL}/predictions?fixture={fid}"
    data = safe_api_call(url)
    res = data.get('response', []) if data else []
    return res[0] if res else None

def get_odds(fid):
    url = f"{BASE_URL}/odds?fixture={fid}"
    data = safe_api_call(url)
    return data.get('response', []) if data else []

# ====================== VALUE BET ======================
def calcul_value_bet(odds_data, prediction):
    values = []

    try:
        preds = prediction.get('predictions', {})
        percent = preds.get('percent', {})

        p_home = float(percent.get('home', '0').replace('%','')) / 100
        p_draw = float(percent.get('draw', '0').replace('%','')) / 100
        p_away = float(percent.get('away', '0').replace('%','')) / 100

        for o in odds_data:
            for bm in o.get('bookmakers', []):
                if bm['name'].lower() not in ['pinnacle','bet365','betway']:
                    continue

                for bet in bm['bets']:
                    if bet['name'] == "Match Winner":
                        for v in bet['values']:
                            odd = float(v['odd'])
                            if odd < 1.30 or odd > 5:
                                continue

                            implied = 1 / odd

                            if v['value'] == 'Home':
                                prob = p_home
                            elif v['value'] == 'Draw':
                                prob = p_draw
                            else:
                                prob = p_away

                            edge = prob - implied

                            print(f"DEBUG → {v['value']} | odd {odd} | prob {prob:.2f} | edge {edge:.3f}", flush=True)

                            if edge > 0.03:
                                values.append(f"{v['value']} @ {odd} | edge {edge*100:.1f}%")

    except Exception as e:
        print(f"❌ VALUE ERROR: {e}", flush=True)

    return "\n".join(values) if values else None

# ====================== TELEGRAM ======================
def send_alert(msg, fid):
    key = f"{fid}_{hash(msg)}"
    if key in sent_alerts:
        return
    sent_alerts.add(key)

    try:
        bot.send_message(CHAT_ID, msg)
        print("✅ Telegram envoyé", flush=True)
    except Exception as e:
        print(f"❌ Telegram error {e}", flush=True)

# ====================== CORE ======================
def check_value_bets():
    print(f"\n⏰ CHECK {datetime.now(timezone.utc)}", flush=True)

    fixtures = get_fixtures()
    print(f"📊 {len(fixtures)} matchs récupérés", flush=True)

    now = datetime.now(timezone.utc)

    analyzed = 0
    found = 0

    for f in fixtures[:120]:

        status = f['fixture']['status']['short']

        # 🔴 FILTRE STATUT STRICT
        if status not in ['NS','1H','HT']:
            continue

        try:
            match_date = datetime.fromisoformat(f['fixture']['date'].replace('Z','+00:00'))
        except:
            continue

        # 🔴 FILTRE TEMPS
        if match_date < now:
            diff = now - match_date
            if diff > timedelta(minutes=15):
                continue
        else:
            # trop loin = inutile
            if match_date > now + timedelta(hours=6):
                continue

        home = f['teams']['home']['name']
        away = f['teams']['away']['name']
        fid = f['fixture']['id']

        analyzed += 1
        print(f"➡️ {home} vs {away} ({status})", flush=True)

        pred = get_predictions(fid)
        odds = get_odds(fid)

        if not pred or not odds:
            continue

        value = calcul_value_bet(odds, pred)

        if value:
            found += 1
            msg = f"🚨 VALUE BET\n{home} vs {away}\n\n{value}"
            send_alert(msg, fid)

    print(f"🔍 Résultat: {analyzed} analysés | {found} values\n", flush=True)

# ====================== LOOP ======================
def run_bot():
    print("🟢 BOT LOOP START", flush=True)
    while True:
        try:
            check_value_bets()
        except Exception as e:
            print(f"❌ LOOP ERROR {e}", flush=True)
        time.sleep(900)  # 15 min

threading.Thread(target=run_bot).start()

# ====================== FLASK ======================
app = Flask(__name__)

@app.route('/')
def home():
    return "APEX-SIRIUS RUNNING"

@app.route('/ping')
def ping():
    return "pong", 200
