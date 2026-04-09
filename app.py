#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════╗
║          APEX-SIRIUS v5.8 — UEFA STATS FIX         ║
║──────────────────────────────────────────────────────────────║
║  Contexte : Les 50 ligues de la whitelist sont activées      ║
║  dans le forfait FootyStats de l'utilisateur.                ║
║  FootyStats /todays-matches retourne donc des données xG     ║
║  réelles pour TOUTES les ligues.                             ║
║                                                              ║
║  Nouveautés vs v5.5 :                                        ║
║  [F24-F28] Hérités de v5.5 (mode dual BET/SIGNAL)           ║
║  [F29] The Odds API intégré comme source de cotes N3         ║
║         → soccer_egypt_premier_league                        ║
║         → soccer_morocco_botola_pro                          ║
║         → soccer_sweden_allsvenskan, et 15+ autres           ║
║  [F30] Mapping league_id → sport_key (50 ligues)             ║
║  [F31] Conversion format Odds API → format Football API      ║
║         → detect_best_value() réutilisé sans modification    ║
║  [F32] Suivi quota Odds API (x-requests-remaining)           ║
║         → Stop automatique si < 10 requêtes restantes        ║
║  [F33] Pipeline cotes 3 niveaux :                            ║
║         1. Football API /odds  (P0/N1/N2 prioritaire)        ║
║         2. The Odds API        (N2/N3 fallback)              ║
║         3. /predictions        (dernier recours → SIGNAL)    ║
╚══════════════════════════════════════════════════════════════╝
"""

import requests
import telebot
import time
import schedule
import os
import threading
import math
import sqlite3
import logging
from flask import Flask
from datetime import datetime, timezone
from difflib import SequenceMatcher

# ====================== LOGGING ======================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("APEX")
log.info("🚀 APEX-SIRIUS v5.8 — UEFA STATS FIX")

# ====================== FLASK ======================
app = Flask(__name__)

@app.route('/')
def home():
    return "APEX-SIRIUS v5.5 Running", 200

@app.route('/ping')
def ping():
    return "pong", 200

# ====================== CONFIG ======================
BOT_TOKEN      = os.environ.get("BOT_TOKEN")
CHAT_ID_RAW    = os.environ.get("CHAT_ID")
API_KEY        = os.environ.get("API_KEY")
FOOTYSTATS_KEY = os.environ.get("FOOTYSTATS_KEY")
DATA_DIR       = os.environ.get("DATA_DIR", "/tmp")

try:
    CHAT_ID = int(CHAT_ID_RAW) if CHAT_ID_RAW else None
except ValueError:
    CHAT_ID = None
    log.error("CHAT_ID invalide")

bot = None
_missing = [k for k, v in {
    "BOT_TOKEN": BOT_TOKEN, "CHAT_ID": CHAT_ID,
    "API_KEY": API_KEY, "FOOTYSTATS_KEY": FOOTYSTATS_KEY
}.items() if not v]

if _missing:
    log.error(f"Variables manquantes : {', '.join(_missing)}")
else:
    try:
        bot = telebot.TeleBot(BOT_TOKEN)
        log.info("Telegram Bot initialise")
    except Exception as e:
        log.error(f"Erreur init Telegram : {e}")

BASE_URL = "https://v3.football.api-sports.io"
HEADERS  = {"x-apisports-key": API_KEY} if API_KEY else {}
FS_BASE  = "https://api.football-data-api.com"

# ====================== ODDS-API.IO — SOURCE COTES ELARGIE ======================
# [F29] odds-api.io : 265 bookmakers, 34 sports, format différent de the-odds-api
# Base URL  : https://api.odds-api.io/v3
# Auth      : ?apiKey=KEY (pas api_key)
# Avantage  : /odds/multi → 10 matchs en 1 seule requête
# Plan free : 2 bookmakers, 100 req/heure
ODDS_API_KEY  = os.environ.get("ODDS_API_KEY", "")
ODDS_API_BASE = "https://api.odds-api.io/v3"

# Bookmakers disponibles selon le plan souscrit
# Free : 2 bookmakers → choisir les plus couverts
ODDS_API_BOOKMAKERS = os.environ.get("ODDS_API_BOOKMAKERS", "Bet365,Unibet")

# Cache des events football du jour (1 appel par cycle)
_oa_events      = []
_oa_events_ts   = 0.0
_oa_events_ttl  = 20 * 60   # 20 min

# Cache des odds par eventId
_oa_odds_cache  = {}
_oa_odds_ts     = 0.0
_oa_odds_ttl    = 10 * 60   # 10 min

def _fetch_oa_events():
    """
    Charge tous les events football à venir via /v3/events.
    1 seul appel par cycle — résultat mis en cache 20 min.
    """
    global _oa_events, _oa_events_ts

    now = time.time()
    if now - _oa_events_ts < _oa_events_ttl and _oa_events:
        return _oa_events

    if not ODDS_API_KEY:
        return []

    try:
        r = requests.get(
            f"{ODDS_API_BASE}/events",
            params={"apiKey": ODDS_API_KEY, "sport": "football"},
            timeout=12
        )
        if r.status_code != 200:
            log.warning(f"odds-api.io /events HTTP {r.status_code}")
            return _oa_events

        data = r.json()
        events = data if isinstance(data, list) else data.get("data", [])
        _oa_events    = events
        _oa_events_ts = now
        log.info(f"odds-api.io events : {len(events)} matchs football")
        return _oa_events

    except Exception as e:
        log.warning(f"_fetch_oa_events : {e}")
        return _oa_events

def _find_oa_event(h_name, a_name, events):
    """
    [F27] Trouve l'event odds-api.io correspondant au match.
    Matching fuzzy sur home/away team names.
    """
    for ev in events:
        eh = ev.get("home", "") or ev.get("home_team", "") or ev.get("homeTeam", "")
        ea = ev.get("away", "") or ev.get("away_team", "") or ev.get("awayTeam", "")
        if fuzzy(h_name, eh) and fuzzy(a_name, ea):
            return ev
    return None

def _fetch_oa_odds_batch(event_ids):
    """
    [F31] /odds/multi — 10 events en 1 requête.
    Retourne {event_id: odds_dict}.
    """
    global _oa_odds_cache, _oa_odds_ts

    now = time.time()
    if now - _oa_odds_ts > _oa_odds_ttl:
        _oa_odds_cache = {}
        _oa_odds_ts    = now

    # Filtrer ceux qui ne sont pas en cache
    missing = [eid for eid in event_ids if eid not in _oa_odds_cache]
    if not missing or not ODDS_API_KEY:
        return _oa_odds_cache

    # Batch par 10 (limite /multi)
    for i in range(0, len(missing), 10):
        batch = missing[i:i+10]
        try:
            r = requests.get(
                f"{ODDS_API_BASE}/odds/multi",
                params={
                    "apiKey":      ODDS_API_KEY,
                    "eventIds":    ",".join(str(e) for e in batch),
                    "bookmakers":  ODDS_API_BOOKMAKERS,
                    "oddsFormat":  "decimal",
                },
                timeout=12
            )
            if r.status_code != 200:
                log.warning(f"odds-api.io /odds/multi HTTP {r.status_code}")
                continue

            results = r.json()
            if isinstance(results, list):
                for item in results:
                    eid = item.get("id")
                    if eid:
                        _oa_odds_cache[eid] = item
            elif isinstance(results, dict):
                eid = results.get("id")
                if eid:
                    _oa_odds_cache[eid] = results

        except Exception as e:
            log.warning(f"_fetch_oa_odds_batch : {e}")

    return _oa_odds_cache

def _parse_oa_odds_to_football_api(odds_item, h_name, a_name):
    """
    [F31] Convertit le format odds-api.io → format Football API /odds.
    Format odds-api.io :
    {
      "bookmakers": {
        "Bet365": [
          {"name": "ML", "odds": [{"home": "2.10", "draw": "3.40", "away": "3.20"}]}
        ]
      }
    }
    → Format Football API : [{"bookmakers": [{"name": bm, "bets": [...]}]}]
    """
    if not odds_item:
        return []

    bookmakers_raw = odds_item.get("bookmakers", {})
    if not bookmakers_raw:
        return []

    bookmakers_out = []
    for bm_name, markets in bookmakers_raw.items():
        bets = []
        for market in markets:
            if market.get("name") not in ("ML", "1X2", "Match Result"):
                continue
            odds_list = market.get("odds", [])
            if not odds_list:
                continue
            o = odds_list[0]
            values = []
            if o.get("home"):
                values.append({"value": "Home", "odd": str(o["home"])})
            if o.get("draw"):
                values.append({"value": "Draw", "odd": str(o["draw"])})
            if o.get("away"):
                values.append({"value": "Away", "odd": str(o["away"])})
            if values:
                bets.append({"name": "Match Winner", "values": values})

        if bets:
            bookmakers_out.append({"name": bm_name, "bets": bets})

    return [{"bookmakers": bookmakers_out}] if bookmakers_out else []

# Index pré-fetché pour le cycle courant
_oa_pending_ids = []   # event_ids à batcher en fin de scan

def get_odds_via_odds_api(league_id, h_name, a_name):
    """
    [F29] Point d'entrée principal odds-api.io.
    Workflow :
    1. Cherche le match dans _oa_events (chargé une fois par cycle)
    2. Fetch /odds/multi pour cet event
    3. Parse et retourne au format Football API
    """
    if not ODDS_API_KEY:
        return []

    events = _oa_events  # déjà chargé par fetch_oa_events_cycle()
    ev     = _find_oa_event(h_name, a_name, events)
    if not ev:
        return []

    eid        = ev.get("id")
    odds_cache = _fetch_oa_odds_batch([eid])
    odds_item  = odds_cache.get(eid)

    return _parse_oa_odds_to_football_api(odds_item, h_name, a_name)

def fetch_oa_events_cycle():
    """
    Appel unique par cycle pour pré-charger les events odds-api.io.
    """
    return _fetch_oa_events()


# ====================== GATE-0 : WHITELIST 50 LIGUES ======================
LEAGUE_WHITELIST = {
    # P0
    2:   ("P0", "UEFA Champions League"),
    3:   ("P0", "UEFA Europa League"),
    848: ("P0", "UEFA Europa Conference League"),
    17:  ("P0", "AFC Champions League"),
    # N1
    39:  ("N1", "Premier League"),
    140: ("N1", "La Liga"),
    78:  ("N1", "Bundesliga"),
    135: ("N1", "Serie A"),
    61:  ("N1", "Ligue 1"),
    # N2
    40:  ("N2", "Championship"),
    62:  ("N2", "Ligue 2"),
    79:  ("N2", "2. Bundesliga"),
    136: ("N2", "Serie B"),
    88:  ("N2", "Eredivisie"),
    144: ("N2", "Pro League Belgique"),
    94:  ("N2", "Primeira Liga"),
    203: ("N2", "Super Lig"),
    179: ("N2", "Scottish Premiership"),
    235: ("N2", "Russian Premier League"),
    71:  ("N2", "Serie A Bresil"),
    128: ("N2", "Primera Division Argentine"),
    262: ("N2", "Liga MX"),
    253: ("N2", "MLS"),
    98:  ("N2", "J1 League"),
    292: ("N2", "K League 1"),
    307: ("N2", "Saudi Professional League"),
    188: ("N2", "A-League Australie"),
    # N3
    41:  ("N3", "EFL League One"),
    89:  ("N3", "Eerste Divisie"),
    113: ("N3", "Allsvenskan"),
    119: ("N3", "Superliga Danemark"),
    103: ("N3", "Eliteserien Norvege"),
    106: ("N3", "Ekstraklasa Pologne"),
    95:  ("N3", "LigaPro Portugal"),
    218: ("N3", "Bundesliga Autriche"),
    207: ("N3", "Super League Suisse"),
    197: ("N3", "Super League Grece"),
    283: ("N3", "Liga I Roumanie"),
    271: ("N3", "NB I Hongrie"),
    210: ("N3", "Prva HNL Croatie"),
    333: ("N3", "Ukrainian Premier League"),
    382: ("N3", "Israeli Premier League"),
    169: ("N3", "Chinese Super League"),
    200: ("N3", "Botola Pro Maroc"),
    233: ("N3", "Egyptian Premier League"),
    265: ("N3", "Primera Division Chili"),
    239: ("N3", "Categoria Primera A Colombie"),
    244: ("N3", "Veikkausliiga Finlande"),
    164: ("N3", "Urvalsdeild Islande"),
    384: ("N3", "Ivory Coast Ligue 1"),
}

TIERS_WITH_ODDS = {"P0", "N1", "N2"}

def get_league_info(league_id):
    return LEAGUE_WHITELIST.get(league_id)

# Tiers qui ont généralement des cotes bookmakers disponibles
TIERS_WITH_ODDS = {"P0", "N1", "N2"}

# ====================== SEUILS ======================
MIN_DCS       = 0.58   # [F26] Unifié — footystats garanti sur 50 ligues
MIN_EDGE      = 0.03   # Mode A (BET)
MIN_CONF      = 15     # Mode A + B
MIN_SIGNAL_P  = 0.50   # Mode B (SIGNAL) — probabilité minimum FootyStats

# ====================== EXCLUSION ======================
EXCLUSION_KEYWORDS = [
    "women", " w ", " w)", "feminin", "femenino", "feminine",
    "girl", "fem ", "u19", "u21", "u23", "u18", "u17", "u16",
    "u15", "reserves", "reserve", "b team", " ii ", " ii)",
    " iii", "youth", "sub-23", "sub23", "amateur", "futsal",
    "indoor", "beach",
]

def is_excluded(name):
    n = name.lower()
    return any(kw in n for kw in EXCLUSION_KEYWORDS)

# ====================== SQLITE ======================
DB_PATH = os.path.join(DATA_DIR, "apex_v58.db")

def init_db():
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS bets (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                ts         TEXT,
                fixture_id INTEGER,
                home       TEXT,
                away       TEXT,
                league_id  INTEGER,
                tier       TEXT,
                mode       TEXT,
                side       TEXT,
                odd        REAL,
                edge       REAL,
                bookie     TEXT,
                prob       REAL,
                hxg        REAL,
                axg        REAL,
                dcs        REAL,
                conf       INTEGER,
                stake      REAL,
                result     TEXT DEFAULT 'PENDING',
                pnl        REAL DEFAULT 0.0
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS bankroll (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                amount REAL NOT NULL
            )
        """)
        c.execute("INSERT OR IGNORE INTO bankroll (id, amount) VALUES (1, 100.0)")
        conn.commit()
        conn.close()
        log.info(f"DB : {DB_PATH}")
    except Exception as e:
        log.error(f"init_db : {e}")

def get_bankroll():
    try:
        conn = sqlite3.connect(DB_PATH)
        row  = conn.execute("SELECT amount FROM bankroll WHERE id=1").fetchone()
        conn.close()
        return row[0] if row else 100.0
    except Exception as e:
        log.warning(f"get_bankroll : {e}")
        return 100.0

def log_bet_db(data):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("""
            INSERT INTO bets
              (ts, fixture_id, home, away, league_id, tier, mode, side,
               odd, edge, bookie, prob, hxg, axg, dcs, conf, stake)
            VALUES
              (:ts, :fixture_id, :home, :away, :league_id, :tier, :mode,
               :side, :odd, :edge, :bookie, :prob, :hxg, :axg, :dcs,
               :conf, :stake)
        """, data)
        conn.commit()
        conn.close()
    except Exception as e:
        log.warning(f"log_bet_db : {e}")

# ====================== [F25] FOOTYSTATS — SOURCE PRIMAIRE ======================
# FootyStats /todays-matches retourne les matchs de toutes les 50 ligues
# activées dans le forfait avec xG inclus dans chaque match.
_fs_matches   = []
_fs_match_ts  = 0.0
FS_TTL        = 25 * 60  # 25 min

def normalize(name):
    n = name.lower().strip()
    for rm in ["fc", "cf", "sc", "ac", "afc", "fk", "sk", "sv",
               "bv", "vv", "if", "rcd", "rc", "sd", "ud", "as"]:
        n = n.replace(f" {rm}", "").replace(f"{rm} ", "")
    return n.strip()

def fuzzy(a, b, threshold=0.80):
    na, nb = normalize(a), normalize(b)
    if na == nb:
        return True
    return SequenceMatcher(None, na, nb).ratio() >= threshold

def fetch_fs_todays_matches():
    """
    [F25] Appel unique à FootyStats /todays-matches par cycle.
    Retourne la liste brute de tous les matchs du jour.
    Chaque match contient team_a_xg_avg et team_b_xg_avg.
    """
    global _fs_matches, _fs_match_ts

    now = time.time()
    if now - _fs_match_ts < FS_TTL and _fs_matches:
        return _fs_matches

    if not FOOTYSTATS_KEY:
        return []

    try:
        r = requests.get(
            f"{FS_BASE}/todays-matches",
            params={"key": FOOTYSTATS_KEY},
            timeout=15
        )
        if r.status_code != 200:
            log.warning(f"FootyStats /todays-matches HTTP {r.status_code}")
            return _fs_matches

        data = r.json().get("data", [])
        _fs_matches  = data
        _fs_match_ts = now
        log.info(f"FootyStats : {len(data)} matchs charges")
        return _fs_matches

    except Exception as e:
        log.warning(f"fetch_fs_todays_matches : {e}")
        return _fs_matches

def find_fs_match(h_name, a_name, fs_matches):
    """
    [F27] Trouve le match FootyStats correspondant à une paire d'équipes.
    Retourne le dict match ou None.
    """
    for m in fs_matches:
        mh = m.get("home_name", "")
        ma = m.get("away_name", "")
        if fuzzy(h_name, mh) and fuzzy(a_name, ma):
            return m
    return None

def get_xg_from_fs_match(fs_match, is_home):
    """
    Extrait xG depuis un match FootyStats.
    team_a_xg_avg = domicile, team_b_xg_avg = extérieur.
    """
    if not fs_match:
        return None
    field = "team_a_xg_avg" if is_home else "team_b_xg_avg"
    val   = fs_match.get(field)
    if val is not None:
        try:
            v = float(val)
            return v if v > 0 else None
        except (ValueError, TypeError):
            pass
    return None

# ====================== GATE-1 : DCS ======================
def calculate_dcs(stats_h, stats_a, hxg_source, axg_source):
    """
    [F26] DCS recalibré.
    Avec FootyStats activé sur 50 ligues, hxg_source sera
    quasi-systématiquement 'footystats'. Le DCS sera donc
    naturellement dans la plage haute (0.76 - 1.00).
    """
    score = 0.0

    try:
        h_p = stats_h['fixtures']['played']['total']
        a_p = stats_a['fixtures']['played']['total']
        mp  = min(h_p, a_p)
        if mp >= 10:   score += 0.40
        elif mp >= 6:  score += 0.25
        elif mp >= 3:  score += 0.10
    except (KeyError, TypeError):
        pass

    score += 0.20 if hxg_source == "footystats" else 0.08
    score += 0.20 if axg_source == "footystats" else 0.08

    try:
        _ = stats_h['goals']['for']['total']['total']
        _ = stats_a['goals']['for']['total']['total']
        score += 0.20
    except (KeyError, TypeError):
        pass

    if hxg_source == "goals_proxy" and axg_source == "goals_proxy":
        score *= 0.80

    return min(round(score, 3), 1.0)

# ====================== MATH : DIXON-COLES ======================
DC_RHO = -0.13

def poisson_prob(lmb, k):
    try:
        if lmb <= 0:
            return 1.0 if k == 0 else 0.0
        return (math.exp(-lmb) * (lmb ** k)) / math.factorial(k)
    except Exception:
        return 0.0

def tau(x, y, lmb, mu, rho):
    if x == 0 and y == 0:   return 1.0 - lmb * mu * rho
    elif x == 1 and y == 0: return 1.0 + mu * rho
    elif x == 0 and y == 1: return 1.0 + lmb * rho
    elif x == 1 and y == 1: return 1.0 - rho
    return 1.0

def calculate_probs(hxg, axg):
    probs = {"H": 0.0, "D": 0.0, "A": 0.0}
    hp = [poisson_prob(hxg, i) for i in range(7)]
    ap = [poisson_prob(axg, i) for i in range(7)]
    for h in range(7):
        for a in range(7):
            t = tau(h, a, hxg, axg, DC_RHO)
            p = max(hp[h] * ap[a] * t, 0.0)
            if   h > a:  probs["H"] += p
            elif h == a: probs["D"] += p
            else:        probs["A"] += p
    total = sum(probs.values())
    if total > 0:
        probs = {k: v / total for k, v in probs.items()}
    return probs

# ====================== ML CONFIDENCE ======================
def calculate_confidence(hxg, axg, tier, edge, dcs):
    score = 0
    diff  = abs(hxg - axg)
    if diff > 1.5:    score += 20
    elif diff > 0.8:  score += 12
    elif diff > 0.4:  score += 6
    score += {"P0": 12, "N1": 10, "N2": 6, "N3": 3}.get(tier, 0)
    if edge > 0.15:   score += 10
    elif edge > 0.10: score += 7
    elif edge > 0.05: score += 4
    if dcs >= 0.80:   score += 8
    elif dcs >= 0.65: score += 5
    elif dcs >= 0.58: score += 3
    return min(score, 50)

# ====================== KELLY ======================
KELLY_FRACTION = 0.25
MAX_STAKE_PCT  = 0.05

def kelly_stake(prob, odd, bankroll):
    b = odd - 1.0
    q = 1.0 - prob
    k = (b * prob - q) / b if b > 0 else 0.0
    if k <= 0:
        return 0.0
    return round(min(bankroll * KELLY_FRACTION * k,
                     bankroll * MAX_STAKE_PCT), 2)

# ====================== MODE A : VALUE ENGINE (avec cotes) ======================
def detect_best_value(probs, odds_data, hxg, axg, tier, dcs):
    best     = None
    max_edge = 0.0

    for bm_data in odds_data:
        for bm in bm_data.get('bookmakers', []):
            bn = bm['name']
            for bet in bm.get('bets', []):
                if bet['name'] != "Match Winner":
                    continue
                for v in bet.get('values', []):
                    try:
                        side = v['value']
                        odd  = float(v['odd'])
                    except (KeyError, ValueError):
                        continue
                    if odd < 1.50:
                        continue
                    key        = "H" if side == "Home" else "D" if side == "Draw" else "A"
                    prob_model = probs.get(key, 0.0)
                    edge       = prob_model - (1.0 / odd)
                    if edge < MIN_EDGE:
                        continue
                    if key == "D" and edge < 0.08:
                        continue
                    if key == "A" and prob_model < 0.30:
                        continue
                    if key == "H" and prob_model < 0.35 and odd < 1.60:
                        continue
                    conf = calculate_confidence(hxg, axg, tier, edge, dcs)
                    if conf < MIN_CONF:
                        continue
                    if edge > max_edge:
                        max_edge = edge
                        best = {
                            "side": side, "key": key, "odd": odd,
                            "edge": edge, "bookie": bn,
                            "conf": conf, "prob": prob_model,
                            "mode": "BET"
                        }
    return best

# ====================== [F24] MODE B : SIGNAL ENGINE (sans cotes) ======================
def detect_signal(probs, predictions, hxg, axg, tier, dcs):
    """
    Mode B : pas de cotes bookmaker.
    Utilise les probabilités de notre modèle Poisson + validation
    par /predictions API-Football si disponible.
    Retourne un signal si P(outcome) > MIN_SIGNAL_P.
    """
    best      = None
    best_prob = MIN_SIGNAL_P

    side_map = {"H": "Home", "D": "Draw", "A": "Away"}

    # Probabilités API-Football /predictions (validation externe)
    api_probs = {}
    if predictions:
        try:
            pw = predictions.get('predictions', {}).get('percent', {})
            api_probs = {
                "H": float(pw.get('home', '0').replace('%', '')) / 100,
                "D": float(pw.get('draws', '0').replace('%', '')) / 100,
                "A": float(pw.get('away', '0').replace('%', '')) / 100,
            }
        except Exception as e:
            log.debug(f"predictions parse : {e}")

    for key in ["H", "A", "D"]:
        pm = probs.get(key, 0.0)

        # Draw : bar plus élevé (trop volatile sans cotes)
        if key == "D":
            continue

        # Validation croisée avec API predictions si disponible
        if api_probs:
            pa = api_probs.get(key, 0.0)
            # Les deux modèles doivent être d'accord (même outcome favori)
            if pa < MIN_SIGNAL_P * 0.85:
                continue

        if pm > best_prob:
            # Pseudo-edge basé sur la force du signal
            pseudo_edge = pm - (1.0 - pm) * 0.5
            conf = calculate_confidence(hxg, axg, tier,
                                        max(pseudo_edge, 0.05), dcs)
            if conf < MIN_CONF:
                continue
            best_prob = pm
            best = {
                "side": side_map[key], "key": key, "odd": None,
                "edge": None, "bookie": None,
                "conf": conf, "prob": pm,
                "mode": "SIGNAL"
            }

    return best

# ====================== API-SPORTS ======================
def safe_get(url, params=None):
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=10)
        if r.status_code == 200:
            return r.json()
        log.debug(f"safe_get HTTP {r.status_code} : {url}")
        return None
    except Exception as e:
        log.warning(f"safe_get : {e}")
        return None

def get_fixtures():
    d = safe_get(f"{BASE_URL}/fixtures",
                 {"date": time.strftime('%Y-%m-%d')})
    return d.get('response', []) if d else []

def get_odds(fid):
    d = safe_get(f"{BASE_URL}/odds", {"fixture": fid})
    return d.get('response', []) if d else []

def get_stats(tid, lid, season):
    d = safe_get(f"{BASE_URL}/teams/statistics",
                 {"team": tid, "league": lid, "season": season})
    return d.get('response') if d else None

def get_predictions(fid):
    """[F24] Mode B — probabilités Football API."""
    d = safe_get(f"{BASE_URL}/predictions", {"fixture": fid})
    resp = d.get('response', []) if d else []
    return resp[0] if resp else None

# ====================== [FIX-UEFA] STATS FALLBACK ======================
# Les compétitions UEFA (UCL, UEL, UECL) n'ont pas de stats dans
# Football API sous leur propre league_id. Les stats sont stockées
# sous la ligue domestique de chaque équipe.
# Ce mapping donne la ligue domestique principale par équipe connue.
# Pour les équipes inconnues : on cherche via /teams?id=X

# Ligue domestique par league_id UEFA → liste de league_ids domestiques à essayer
UEFA_LEAGUE_IDS = {2, 3, 848, 17}   # UCL, UEL, UECL, AFC CL

def get_stats_smart(tid, league_id, season):
    """
    [FIX-UEFA] Récupère les stats en cherchant dans :
    1. La ligue de la compétition (ex: UEL league_id=3)
    2. Si vide et compétition UEFA → chercher dans la ligue domestique
       via /teams?id={tid} pour trouver la ligue actuelle
    3. Fallback sur les top5 domestic leagues si toujours vide
    """
    # Tentative directe d'abord
    stats = get_stats(tid, league_id, season)
    if stats:
        return stats

    # Si pas de compétition UEFA → pas de fallback
    if league_id not in UEFA_LEAGUE_IDS:
        return None

    # [FIX] Chercher la ligue domestique via /teams
    try:
        d = safe_get(f"{BASE_URL}/teams", {"id": tid})
        if d:
            response = d.get('response', [])
            if response:
                country = response[0].get('team', {}).get('country', '')
                # Mapper pays → league_id domestique prioritaire
                country_to_league = {
                    'England':     39,
                    'Spain':       140,
                    'Germany':     78,
                    'Italy':       135,
                    'France':      61,
                    'Portugal':    94,
                    'Netherlands': 88,
                    'Belgium':     144,
                    'Scotland':    179,
                    'Turkey':      203,
                    'Russia':      235,
                    'Ukraine':     333,
                }
                domestic_lid = country_to_league.get(country)
                if domestic_lid:
                    stats = get_stats(tid, domestic_lid, season)
                    if stats:
                        log.debug(f"  Stats fallback [{country} → lid={domestic_lid}]")
                        return stats
    except Exception as e:
        log.debug(f"get_stats_smart fallback : {e}")

    # Dernier recours : essayer les top5 un par un
    for fallback_lid in [39, 140, 78, 135, 61, 94, 88, 203]:
        if fallback_lid == league_id:
            continue
        stats = get_stats(tid, fallback_lid, season)
        if stats:
            log.debug(f"  Stats brute-force lid={fallback_lid} pour team {tid}")
            return stats

    return None



# ====================== CHECK LOOP ======================
def check_loop():
    log.info(f"Cycle v5.8 — {datetime.now().strftime('%H:%M')}")

    # [F25] Pre-fetch FootyStats UNE SEULE FOIS
    fs_matches = fetch_fs_todays_matches()

    # Pre-fetch odds-api.io events UNE SEULE FOIS par cycle
    _oa_events_global = fetch_oa_events_cycle() if ODDS_API_KEY else []
    oa_ok = len(_oa_events_global) > 0
    if oa_ok:
        log.info(f"odds-api.io events : {len(_oa_events_global)} matchs")
    fs_ok      = len(fs_matches) > 0
    log.info(f"FootyStats : {'actif — ' + str(len(fs_matches)) + ' matchs' if fs_ok else 'hors ligne'}")

    fixtures = get_fixtures()
    now      = datetime.now(timezone.utc)
    bank     = get_bankroll()
    sent     = 0

    for f in fixtures:
        try:
            league_id = f['league']['id']

            # GATE-0
            league_info = get_league_info(league_id)
            if not league_info:
                continue
            tier, league_name = league_info

            # Fenetre temporelle
            m_date  = datetime.fromisoformat(
                f['fixture']['date'].replace('Z', '+00:00'))
            delta_h = (m_date - now).total_seconds() / 3600
            if not (0 < delta_h < 6):
                continue

            h_name = f['teams']['home']['name']
            a_name = f['teams']['away']['name']

            if is_excluded(h_name) or is_excluded(a_name):
                continue

            # Stats API-Football
            season  = f['league']['season']
            stats_h = get_stats_smart(f['teams']['home']['id'], league_id, season)
            stats_a = get_stats_smart(f['teams']['away']['id'], league_id, season)
            if not stats_h or not stats_a:
                continue

            # [F27] Cross-match FootyStats → xG réel
            hxg_source = axg_source = "goals_proxy"
            fs_match   = find_fs_match(h_name, a_name, fs_matches) if fs_ok else None

            hxg = get_xg_from_fs_match(fs_match, is_home=True)
            if hxg:
                hxg_source = "footystats"
            else:
                try:
                    h_p = stats_h['fixtures']['played']['total']
                    h_g = stats_h['goals']['for']['total']['total']
                    hxg = (h_g / h_p * 1.10) if h_p > 0 else 1.20
                except (KeyError, TypeError, ZeroDivisionError):
                    hxg = 1.20

            axg = get_xg_from_fs_match(fs_match, is_home=False)
            if axg:
                axg_source = "footystats"
            else:
                try:
                    a_p = stats_a['fixtures']['played']['total']
                    a_g = stats_a['goals']['for']['total']['total']
                    axg = (a_g / a_p) if a_p > 0 else 1.00
                except (KeyError, TypeError, ZeroDivisionError):
                    axg = 1.00

            hxg = max(float(hxg), 0.30)
            axg = max(float(axg), 0.30)

            # GATE-1 : DCS
            dcs = calculate_dcs(stats_h, stats_a, hxg_source, axg_source)
            if dcs < MIN_DCS:
                log.info(f"  DCS={dcs:.2f} trop bas [{h_name} vs {a_name}]")
                continue
            log.info(f"  ✅ DCS={dcs:.2f} OK [{h_name} vs {a_name}] xG:{hxg:.2f}/{axg:.2f} src:{hxg_source}/{axg_source}")

            probs   = calculate_probs(hxg, axg)
            fid     = f['fixture']['id']
            result  = None

            # ── PIPELINE COTES 3 NIVEAUX ─────────────────────────────
            # [F33] Niveau 1 : Football API /odds (P0/N1/N2 prioritaire)
            odds_data = []
            if tier in TIERS_WITH_ODDS:
                odds_data = get_odds(fid)
                if odds_data:
                    log.debug(f"  Cotes : Football API [{league_name}]")

            # [F33] Niveau 2 : The Odds API (fallback N2/N3)
            if not odds_data and ODDS_API_KEY:
                odds_data = get_odds_via_odds_api(league_id, h_name, a_name)
                if odds_data:
                    log.info(f"  Cotes : Odds API [{league_name}]")

            # Tentative BET si cotes disponibles
            if odds_data:
                log.info(f"  Odds trouvees [{h_name} vs {a_name}] : {len(odds_data)} bookmakers")
                result = detect_best_value(
                    probs, odds_data, hxg, axg, tier, dcs)
                if result:
                    log.info(f"  Value detectee : {result['side']} edge={result['edge']*100:.1f}%")
                else:
                    log.info(f"  Aucune value (edge<{MIN_EDGE*100:.0f}% ou conf<{MIN_CONF})")
            else:
                log.info(f"  Aucune cote [{h_name} vs {a_name}] → Mode SIGNAL")

            # [F33] Niveau 3 : /predictions → Mode SIGNAL (dernier recours)
            if result is None:
                predictions = get_predictions(fid)
                result = detect_signal(
                    probs, predictions, hxg, axg, tier, dcs)

            if result and sent < 10:
                mode  = result['mode']
                stake = kelly_stake(result['prob'],
                                    result['odd'] or 0, bank) if mode == "BET" else 0.0

                src_h = "🟢" if hxg_source == "footystats" else "🟡"
                src_a = "🟢" if axg_source == "footystats" else "🟡"

                if mode == "BET":
                    # [F28] Alerte BET complète
                    msg = (
                        f"🚀 APEX-SIRIUS v5.8 — BET\n"
                        f"━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🏆 {league_name} [{tier}]\n"
                        f"⚽ {h_name} vs {a_name}\n"
                        f"━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🎯 Paris : {result['side']} @ {result['odd']:.2f}\n"
                        f"📚 Bookmaker : {result['bookie']}\n"
                        f"━━━━━━━━━━━━━━━━━━━━━\n"
                        f"📊 xG Dom. : {hxg:.2f} {src_h} ({hxg_source})\n"
                        f"📊 xG Ext. : {axg:.2f} {src_a} ({axg_source})\n"
                        f"💡 P(modele) : {result['prob']*100:.1f}%\n"
                        f"💰 Edge : +{result['edge']*100:.1f}%\n"
                        f"🧠 ML Score : {result['conf']}/50\n"
                        f"🔬 DCS : {dcs:.2f}\n"
                        f"━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🏦 Bankroll : {bank:.2f}u\n"
                        f"📌 Mise Kelly : {stake:.2f}u ({stake/bank*100:.1f}%)\n"
                        f"⏱ Kick-off : {m_date.strftime('%H:%M')} UTC"
                    )
                else:
                    # [F28] Alerte SIGNAL — pas de cotes
                    msg = (
                        f"📡 APEX-SIRIUS v5.8 — SIGNAL\n"
                        f"━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🏆 {league_name} [{tier}]\n"
                        f"⚽ {h_name} vs {a_name}\n"
                        f"━━━━━━━━━━━━━━━━━━━━━\n"
                        f"📡 Signal : {result['side']}\n"
                        f"⚠️ Cotes non disponibles via API\n"
                        f"  → Verifier manuellement\n"
                        f"━━━━━━━━━━━━━━━━━━━━━\n"
                        f"📊 xG Dom. : {hxg:.2f} {src_h} ({hxg_source})\n"
                        f"📊 xG Ext. : {axg:.2f} {src_a} ({axg_source})\n"
                        f"💡 P(modele) : {result['prob']*100:.1f}%\n"
                        f"🧠 ML Score : {result['conf']}/50\n"
                        f"🔬 DCS : {dcs:.2f}\n"
                        f"━━━━━━━━━━━━━━━━━━━━━\n"
                        f"⏱ Kick-off : {m_date.strftime('%H:%M')} UTC\n"
                        f"🟢 footystats  🟡 proxy"
                    )

                if bot and CHAT_ID:
                    try:
                        bot.send_message(CHAT_ID, msg)
                        log.info(
                            f"[{mode}] {result['side']}"
                            f" | {league_name} [{tier}]"
                            f" | xG {hxg_source}/{axg_source}"
                            f" | DCS={dcs:.2f}"
                        )
                        log_bet_db({
                            "ts":         datetime.now().isoformat(),
                            "fixture_id": fid,
                            "home":       h_name,
                            "away":       a_name,
                            "league_id":  league_id,
                            "tier":       tier,
                            "mode":       mode,
                            "side":       result['side'],
                            "odd":        result.get('odd') or 0.0,
                            "edge":       result.get('edge') or 0.0,
                            "bookie":     result.get('bookie') or "",
                            "prob":       result['prob'],
                            "hxg":        hxg,
                            "axg":        axg,
                            "dcs":        dcs,
                            "conf":       result['conf'],
                            "stake":      stake,
                        })
                        sent += 1
                    except Exception as e:
                        log.error(f"send_message : {e}")

        except Exception as e:
            fid = f.get('fixture', {}).get('id', '?')
            log.warning(f"Loop [{fid}] : {e}")

    log.info(
        f"Cycle termine — {sent} alerte(s)"
        f" — Bankroll : {bank:.2f}u"
    )

# ====================== SCHEDULER ======================
def safe_check():
    try:
        check_loop()
    except Exception as e:
        log.error(f"check_loop crash : {e}")

def run():
    time.sleep(15)
    safe_check()
    schedule.every(15).minutes.do(safe_check)
    while True:
        try:
            schedule.run_pending()
        except Exception as e:
            log.error(f"Scheduler : {e}")
        time.sleep(1)

# ====================== ENTRYPOINT ======================
init_db()

if bot:
    threading.Thread(target=run, daemon=True).start()
    log.info("Scheduler demarre (cycle 15 min)")
else:
    log.warning("Bot non initialise — scheduler non demarre")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
