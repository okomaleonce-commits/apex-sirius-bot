import requests
import telebot
import time
import schedule
import os
import sys
import threading
import random
import math
from flask import Flask, render_template_string
from datetime import datetime, timezone, timedelta

print("🚀 APEX-ENGINE v1.0 - PROTOCOL A-LAP IMPLEMENTATION", flush=True)

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

# ====================== A-LAP CONSTANTS ======================
LEAGUE_AVG_GOALS = 2.65  # Ajustable par ligue, défaut A-LAP
HOME_ADVANTAGE = 1.10    # Facteur domicile
RHO = 0.10               # Paramètre Dixon-Coles
N_SIMULATIONS = 10000    # Nombre de simulations Monte-Carlo

# ====================== API HANDLER ======================
def safe_api_call(url):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
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

def get_team_stats(team_id, league_id, season):
    url = f"{BASE_URL}/teams/statistics?team={team_id}&league={league_id}&season={season}"
    data = safe_api_call(url)
    return data.get('response', []) if data else None

def get_odds(fixture_id):
    url = f"{BASE_URL}/odds?fixture={fixture_id}"
    data = safe_api_call(url)
    return data.get('response', []) if data else []

# ====================== APEX-ENGINE CORE (A-LAP) ======================

def poisson_prob(lmbda, k):
    """Calcul probabilité Poisson P(k; lambda)"""
    return (math.exp(-lmbda) * (lmbda ** k)) / math.factorial(k)

def calculate_strength_model(stats_home, stats_away):
    """
    Module 4 & 5 A-LAP : Calcul des forces et xG
    """
    try:
        # Extraction données brutes
        # Domicile
        home_goals_for = float(stats_home['goals']['for']['total']['total'])
        home_goals_against = float(stats_home['goals']['against']['total']['total'])
        home_played = float(stats_home['fixtures']['played']['total'])
        if home_played == 0: return None, None
        
        home_avg_for = home_goals_for / home_played
        home_avg_against = home_goals_against / home_played

        # Extérieur
        away_goals_for = float(stats_away['goals']['for']['total']['total'])
        away_goals_against = float(stats_away['goals']['against']['total']['total'])
        away_played = float(stats_away['fixtures']['played']['total'])
        if away_played == 0: return None, None

        away_avg_for = away_goals_for / away_played
        away_avg_against = away_goals_against / away_played

        # Calcul des Forces (Module 5.1)
        home_attack = home_avg_for / LEAGUE_AVG_GOALS
        home_defense = home_avg_against / LEAGUE_AVG_GOALS
        
        away_attack = away_avg_for / LEAGUE_AVG_GOALS
        away_defense = away_avg_against / LEAGUE_AVG_GOALS

        # Calcul xG (Module 5.2)
        home_xg = home_attack * away_defense * LEAGUE_AVG_GOALS * HOME_ADVANTAGE
        away_xg = away_attack * home_defense * LEAGUE_AVG_GOALS

        # Ajustement Forme (Optionnel si données disponibles)
        # Pour l'instant on garde le modèle de base robuste

        return home_xg, away_xg

    except Exception as e:
        print(f"⚠️ Erreur Strength Model: {e}", flush=True)
        return None, None

def run_monte_carlo(home_xg, away_xg):
    """
    Module 5.4 A-LAP : Simulation Monte-Carlo avec correction Dixon-Coles
    """
    results = {"H": 0, "D": 0, "A": 0, "BTTS": 0, "O25": 0}
    
    # Pré-calcul des matrices de probabilité Poisson (0 à 6 buts)
    # Optimisation: on pré-calcule les probas de base
    home_probs = [poisson_prob(home_xg, i) for i in range(7)]
    away_probs = [poisson_prob(away_xg, i) for i in range(7)]

    # Simulation
    for h in range(7):
        for a in range(7):
            # Probabilité de ce score
            prob = home_probs[h] * away_probs[a]
            
            # Correction Dixon-Coles (Module 5.3)
            if h == 0 and a == 0:
                prob *= (1 - RHO)
            elif h == 0 and a == 1:
                prob *= (1 + RHO)
            elif h == 1 and a == 0:
                prob *= (1 + RHO)
            elif h == 1 and a == 1:
                prob *= (1 - RHO)
            
            # Agrégation
            if h > a:
                results["H"] += prob
            elif h == a:
                results["D"] += prob
            else:
                results["A"] += prob
            
            if h >= 1 and a >= 1:
                results["BTTS"] += prob
            
            if (h + a) >= 3:
                results["O25"] += prob

    return results

def calculate_value_bet(model_probs, odds_data):
    """
    Module 5.5 A-LAP : Détection Value Bet
    """
    values = []
    try:
        if not odds_data or not odds_data[0].get('bookmakers'):
            return None

        for bm in odds_data[0].get('bookmakers', []):
            if bm['name'].lower() not in ['pinnacle', 'bet365', 'betway']:
                continue
            
            for bet_group in bm['bets']:
                if bet_group['name'] == "Match Winner":
                    for v in bet_group['values']:
                        odd = float(v['odd'])
                        if odd < 1.50: continue # Règle APEX-SIRIUS
                        
                        implied = 1 / odd
                        edge = 0
                        
                        if v['value'] == 'Home':
                            edge = model_probs['H'] - implied
                            if edge > 0.05: # Seuil 5%
                                values.append(f"🏠 HOME VALUE: {model_probs['H']*100:.1f}% vs Odd {odd} (Edge +{edge*100:.1f}%)")
                        elif v['value'] == 'Draw':
                            edge = model_probs['D'] - implied
                            if edge > 0.05:
                                values.append(f"⚖️ DRAW VALUE: {model_probs['D']*100:.1f}% vs Odd {odd} (Edge +{edge*100:.1f}%)")
                        elif v['value'] == 'Away':
                            edge = model_probs['A'] - implied
                            if edge > 0.05:
                                values.append(f"🏃 AWAY VALUE: {model_probs['A']*100:.1f}% vs Odd {odd} (Edge +{edge*100:.1f}%)")
                
                # Ajout Over 2.5
                if bet_group['name'] == "Goals Over/Under":
                     for v in bet_group['values']:
                         if v['value'] == 'Over 2.5':
                             odd = float(v['odd'])
                             implied = 1 / odd
                             edge = model_probs['O25'] - implied
                             if edge > 0.05:
                                 values.append(f"🔥 OVER 2.5 VALUE: {model_probs['O25']*100:.1f}% vs Odd {odd}")

    except Exception as e:
        pass
    
    return "\n".join(values) if values else None

# ====================== NOTIFICATION ======================
def envoyer_notification(message, fixture_id, league, date_time, dcs_score):
    alert_key = f"{fixture_id}"
    if alert_key in sent_alerts: return
    sent_alerts.add(alert_key)

    full_msg = f"""🚨 APEX-ENGINE ALERT (A-LAP v1.0)

🏆 {league}
🕒 {date_time} (UTC)
📡 DCS: {dcs_score}/100

{message}"""

    try:
        bot.send_message(CHAT_ID, full_msg)
        print(f"✅ Telegram envoyé pour {fixture_id}", flush=True)
        value_bets_history.append({"time": datetime.now().strftime("%H:%M"), "message": full_msg})
    except Exception as e:
        print(f"❌ Erreur Telegram: {e}", flush=True)

# ====================== CHECK ======================
def check_value_bets():
    print(f"\n⏰ Check A-LAP à {datetime.now(timezone.utc).strftime('%H:%M:%S')}", flush=True)
    fixtures = get_fixtures()
    if not fixtures: return
    
    now = datetime.now(timezone.utc)
    candidates = []

    # 1. Filtrage temporel
    for fixture in fixtures:
        if fixture['fixture']['status']['short'] not in ["NS", "TBD"]:
            continue
        try:
            match_date = datetime.fromisoformat(fixture['fixture']['date'].replace('Z', '+00:00'))
            if timedelta(minutes=0) < (match_date - now) < timedelta(minutes=60):
                candidates.append(fixture)
        except:
            continue

    print(f"🎯 {len(candidates)} matchs PRE-MATCH détectés.", flush=True)
    
    # Limitation Quota (Stat call est cher: 2 par match)
    # 15 matchs * 3 calls = 45 calls par check.
    LIMIT = 15
    
    for fixture in candidates[:LIMIT]:
        fid = fixture['fixture']['id']
        league_name = fixture['league']['name']
        league_id = fixture['league']['id']
        season = fixture['league']['season']
        home_id = fixture['teams']['home']['id']
        away_id = fixture['teams']['away']['id']
        date_time = fixture['fixture']['date'][:16].replace('T', ' ')
        
        # Gate P1 - Data Check
        # Récupération Stats (Heavy Call)
        stats_home = get_team_stats(home_id, league_id, season)
        stats_away = get_team_stats(away_id, league_id, season)
        
        dcs_score = 0
        if stats_home and stats_away:
            dcs_score = 80
