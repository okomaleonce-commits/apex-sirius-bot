import requests
import telebot
import time
import schedule
import os
import threading
from flask import Flask, render_template_string
from datetime import datetime

print("🚀 app.py chargé sur Render - démarrage du bot...")

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
    "UEFA Europa Conference League"
}

# ====================== NOUVELLE FONCTION : LISTE DES MATCHS DU JOUR ======================
def send_daily_matches_summary():
    print("📅 Envoi de la liste quotidienne des matchs...")
    fixtures = get_fixtures(live=False)
    
    if not fixtures:
        bot.send_message(CHAT_ID, "❌ Aucun match trouvé aujourd'hui.")
        return

    message = "📋 **MATCHS DU JOUR - LIGUES MAJEURES**\n\n"
    message += "DATE | LIGUE | HEURE | HOME | AWAY | STADIUM\n"
    message += "────────────────────────────────────────────────\n"

    for f in fixtures:
        league = f['league']['name']
        if league not in ALLOWED_LEAGUES:
            continue

        date = f['fixture']['date'][:10]
        hour = f['fixture']['date'][11:16]
        home = f['teams']['home']['name']
        away = f['teams']['away']['name']
        stadium = f.get('fixture', {}).get('venue', {}).get('name', 'N/A')

        line = f"{date} | {league} | {hour} | {home} | {away} | {stadium}\n"
        message += line

    # Envoi en un seul message
    try:
        bot.send_message(CHAT_ID, message, parse_mode="Markdown")
        print("✅ Liste des matchs envoyée avec succès !")
    except Exception as e:
        print(f"❌ Erreur envoi liste matchs : {e}")

# ====================== FONCTIONS EXISTANTES (inchangées) ======================
def safe_api_call(url, retries=2):
    for i in range(retries):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=10)
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 429:
                print("🛑 QUOTA API ATTEINT")
                return None
        except:
            pass
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

# (le reste de ton code value bets, dashboard, etc. reste identique)

# ====================== SCHEDULER ======================
def run_scheduler():
    print("🗓️ Scheduler démarré...")

    # Liste des matchs tous les jours à 11:20
    schedule.every().day.at("11:20").do(send_daily_matches_summary)

    # Analyse value bets toutes les 30 minutes (comme avant)
    schedule.every(30).minutes.do(check_value_bets)

    # Premier envoi immédiat pour tester
    send_daily_matches_summary()

    while True:
        schedule.run_pending()
        time.sleep(1)

threading.Thread(target=run_scheduler, daemon=True).start()

# ====================== FLASK ======================
app = Flask(__name__)

@app.route('/')
def home():
    return "🤖 APEX-SIRIUS Bot is running 24/7 !"

@app.route('/ping')
def ping():
    return "pong", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)