import requests
import telebot
import time
import schedule
from config import BOT_TOKEN, CHAT_ID, API_KEY

bot = telebot.TeleBot(BOT_TOKEN)
BASE_URL = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": API_KEY}

# Cache anti-spam
sent_alerts = set()

# ====================== FILTRE LIGUES ======================
# Grandes ligues + ligues secondaires (comme tu as demandé)
ALLOWED_LEAGUES = {
    "Premier League", "Championship",
    "La Liga", "Segunda División",
    "Bundesliga", "2. Bundesliga",
    "Serie A", "Serie B",
    "Ligue 1", "Ligue 2",
    "Eredivisie",
    "Primeira Liga",
    "Champions League",
    "Europa League",
    "UEFA Europa Conference League"
}

def safe_api_call(url, retries=3):
    for i in range(retries):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=10)
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 429:
                wait = 2 ** i
                print(f"⏳ Rate-limit → pause {wait}s")
                time.sleep(wait)
                continue
            else:
                print(f"❌ API erreur {resp.status_code}")
        except Exception as e:
            print(f"⚠️ Erreur : {e}")
            if i == retries - 1:
                envoyer_notification_simple(f"⚠️ API déconnectée")
            time.sleep(1)
    return None

def envoyer_notification(message, fixture_id):
    alert_key = f"{fixture_id}_{hash(message)}"
    if alert_key in sent_alerts:
        return
    sent_alerts.add(alert_key)
    try:
        bot.send_message(CHAT_ID, message)
        print(f"✅ Notification envoyée → {fixture_id}")
    except Exception as e:
        print(f"❌ Telegram erreur : {e}")

def envoyer_notification_simple(message):
    try:
        bot.send_message(CHAT_ID, message)
    except:
        pass

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

def is_prediction_reliable(fixture, prediction):
    if not prediction:
        return False
    # Filtre ligues (grandes + secondaires)
    league_name = fixture.get('league', {}).get('name')
    if league_name not in ALLOWED_LEAGUES:
        return False
    return True

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
                
                # ================= 1X2 =================
                if bet_name == "Match Winner":
                    pred_home = float(prediction['predictions']['home']) / 100
                    pred_draw = float(prediction['predictions']['draw']) / 100
                    pred_away = float(prediction['predictions']['away']) / 100
                    
                    for v in values_list:
                        odd = float(v['odd'])
                        if odd <= 1.01: continue
                        implied = 1 / odd
                        edge = 0.05
                        if v['value'] == 'Home' and pred_home > implied + edge:
                            values.append(f"🏠 HOME VALUE : {pred_home*100:.1f}% vs {odd} (edge {(pred_home-implied)*100:.1f}%)")
                        elif v['value'] == 'Draw' and pred_draw > implied + edge:
                            values.append(f"⚖️ DRAW VALUE : {pred_draw*100:.1f}% vs {odd} (edge {(pred_draw-implied)*100:.1f}%)")
                        elif v['value'] == 'Away' and pred_away > implied + edge:
                            values.append(f"🏃 AWAY VALUE : {pred_away*100:.1f}% vs {odd} (edge {(pred_away-implied)*100:.1f}%)")
                
                # ================= OVER 2.5 =================
                elif bet_name == "Over/Under" and any(v['value'] == 'Over 2.5' for v in values_list):
                    for v in values_list:
                        if v['value'] == 'Over 2.5':
                            odd = float(v['odd'])
                            if odd <= 1.01: continue
                            implied = 1 / odd
                            # Probabilité estimée conservatrice (moyenne foot ≈ 53-55%)
                            pred_over = 0.55
                            edge = 0.05
                            if pred_over > implied + edge:
                                values.append(f"🔥 OVER 2.5 VALUE : {pred_over*100:.1f}% vs {odd} (edge {(pred_over-implied)*100:.1f}%)")
                
                # ================= BTTS =================
                elif bet_name == "Both Teams To Score":
                    for v in values_list:
                        if v['value'] == 'Yes':
                            odd = float(v['odd'])
                            if odd <= 1.01: continue
                            implied = 1 / odd
                            pred_btts = 0.52   # moyenne ≈ 52%
                            edge = 0.05
                            if pred_btts > implied + edge:
                                values.append(f"🤝 BTTS YES VALUE : {pred_btts*100:.1f}% vs {odd} (edge {(pred_btts-implied)*100:.1f}%)")
    except:
        pass
    
    return "\n".join(values) if values else None

def check_value_bets():
    print("🔍 Analyse des matchs (grandes + secondaires ligues)...")
    
    # Pré-match
    fixtures = get_fixtures(live=False)
    for fixture in fixtures:
        fid = fixture['fixture']['id']
        pred = get_predictions(fid)
        if not is_prediction_reliable(fixture, pred):
            continue
        odds = get_odds(fid)
        value_msg = calcul_value_bet(odds, pred, fid)
        if value_msg:
            league = fixture['league']['name']
            match = f"{fixture['teams']['home']['name']} vs {fixture['teams']['away']['name']}"
            msg = f"🚨 VALUE BET (Pré-match)\n\n{league}\n{match}\n{value_msg}\n\n📅 {fixture['fixture']['date'][:16]}"
            envoyer_notification(msg, fid)
    
    # Live
    live_fixtures = get_fixtures(live=True)
    for fixture in live_fixtures:
        fid = fixture['fixture']['id']
        pred = get_predictions(fid)
        if not is_prediction_reliable(fixture, pred):
            continue
        odds = get_odds(fid)
        value_msg = calcul_value_bet(odds, pred, fid)
        if value_msg:
            league = fixture['league']['name']
            match = f"{fixture['teams']['home']['name']} vs {fixture['teams']['away']['name']}"
            score = f"{fixture.get('goals', {}).get('home', '?')}-{fixture.get('goals', {}).get('away', '?')}"
            msg = f"🔴 VALUE BET LIVE\n\n{league}\n{match} ({score})\n{value_msg}"
            envoyer_notification(msg, fid)

# ====================== LANCEMENT ======================
if __name__ == "__main__":
    print("🤖 Bot APEX-SIRIUS v3 démarré ! (grandes + secondaires ligues + Over 2.5 + BTTS)")
    schedule.every(5).minutes.do(check_value_bets)
    check_value_bets()  # check immédiat
    
    while True:
        schedule.run_pending()
        time.sleep(1)