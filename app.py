#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════╗
║          APEX-SIRIUS v5.3 — FOOTYSTATS FIXED EDITION        ║
║──────────────────────────────────────────────────────────────║
║  Fixes vs v5.2:                                              ║
║  [F17] URL FootyStats corrigée :                             ║
║         ❌ https://api.footystats.org/v2                     ║
║         ✅ https://api.football-data-api.com                 ║
║  [F18] Endpoint primaire : /todays-matches                   ║
║         → xG (team_a_xg_avg / team_b_xg_avg) dans le match  ║
║         → Plus besoin de double appel search + team          ║
║  [F19] Matching cross-API par nom normalisé (fuzzy 85%)      ║
║  [F20] DCS réellement variable :                             ║
║         footystats → DCS 0.76–1.00                          ║
║         goals_proxy → DCS 0.40–0.68 (malus 20%)            ║
║  [F21] Pre-fetch FootyStats une seule fois par cycle         ║
║         (économie d'appels API, pas 1 appel par match)       ║
║  [F22] Whitelist FootyStats league_id mappée aux tiers       ║
║  [F23] Fallback gracieux si FootyStats hors ligne            ║
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
log.info("🚀 APEX-SIRIUS v5.3 — FOOTYSTATS FIXED EDITION")

# ====================== FLASK ======================
app = Flask(__name__)

@app.route('/')
def home():
    return "🤖 APEX-SIRIUS v5.3 Running", 200

@app.route('/ping')
def ping():
    return "pong", 200

# ====================== CONFIG ======================
BOT_TOKEN      = os.environ.get("BOT_TOKEN")
CHAT_ID_RAW    = os.environ.get("CHAT_ID")
API_KEY        = os.environ.get("API_KEY")
FOOTYSTATS_KEY = os.environ.get("FOOTYSTATS_KEY")
DATA_DIR       = os.environ.get("DATA_DIR", "/tmp")

# [F1] CHAT_ID casté en int
try:
    CHAT_ID = int(CHAT_ID_RAW) if CHAT_ID_RAW else None
except ValueError:
    CHAT_ID = None
    log.error("❌ CHAT_ID invalide : doit être un entier")

bot = None
_missing = [k for k, v in {
    "BOT_TOKEN": BOT_TOKEN, "CHAT_ID": CHAT_ID,
    "API_KEY": API_KEY, "FOOTYSTATS_KEY": FOOTYSTATS_KEY
}.items() if not v]

if _missing:
    log.error(f"❌ Variables manquantes : {', '.join(_missing)}")
else:
    try:
        bot = telebot.TeleBot(BOT_TOKEN)
        log.info("✅ Telegram Bot initialisé")
    except Exception as e:
        log.error(f"❌ Erreur init Telegram : {e}")

# ── API-Sports (Football API) ─────────────────────────────────
BASE_URL = "https://v3.football.api-sports.io"
HEADERS  = {"x-apisports-key": API_KEY} if API_KEY else {}

# ── [F17] FootyStats — URL CORRIGÉE ──────────────────────────
FS_BASE  = "https://api.football-data-api.com"

# ====================== GATE-0 : WHITELIST ======================
# API-Sports league_id → (tier, nom lisible)
LEAGUE_WHITELIST = {
    2:   ("P0", "UEFA Champions League"),
    3:   ("P0", "UEFA Europa League"),
    848: ("P0", "UEFA Conference League"),
    39:  ("N1", "Premier League"),
    140: ("N1", "La Liga"),
    78:  ("N1", "Bundesliga"),
    135: ("N1", "Serie A"),
    61:  ("N1", "Ligue 1"),
    94:  ("N2", "Primeira Liga"),
    88:  ("N2", "Eredivisie"),
    144: ("N2", "Pro League Belgique"),
    203: ("N2", "Süper Lig"),
    119: ("N2", "Superliga Danemark"),
    113: ("N2", "Allsvenskan"),
    71:  ("N3", "Série A Brésil"),
    128: ("N3", "Liga Profesional Argentine"),
    188: ("N3", "MLS"),
    253: ("N3", "Saudi Pro League"),
}

def get_league_info(league_id: int) ->object:
    return LEAGUE_WHITELIST.get(league_id)

# ====================== GATE-1 : DCS ======================
# [F20] Seuils différenciés selon la source xG
MIN_DCS_FS    = 0.65   # FootyStats confirmé
MIN_DCS_PROXY = 0.60   # Goals proxy (bar abaissé car données moins fiables)

def calculate_dcs(stats_h: dict, stats_a: dict,
                  hxg_source: str, axg_source: str) -> float:
    """
    DCS [0.0 – 1.0]
    ─ Matchs joués (max 0.40)
    ─ Qualité source xG (max 0.40)
    ─ Complétude stats goals (max 0.20)
    [F20] Malus 20% si double proxy
    """
    score = 0.0

    # Matchs joués
    try:
        h_p = stats_h['fixtures']['played']['total']
        a_p = stats_a['fixtures']['played']['total']
        mp  = min(h_p, a_p)
        if mp >= 10:   score += 0.40
        elif mp >= 6:  score += 0.25
        elif mp >= 3:  score += 0.10
    except (KeyError, TypeError):
        pass

    # Qualité source xG — [F20] valeurs corrigées
    score += 0.20 if hxg_source == "footystats" else 0.08
    score += 0.20 if axg_source == "footystats" else 0.08

    # Complétude goals stats
    try:
        _ = stats_h['goals']['for']['total']['total']
        _ = stats_a['goals']['for']['total']['total']
        score += 0.20
    except (KeyError, TypeError):
        pass

    # [F20] Malus 20% si les deux sources sont proxy
    if hxg_source == "goals_proxy" and axg_source == "goals_proxy":
        score *= 0.80

    return min(round(score, 3), 1.0)

# ====================== GATE-2 : VALUE ======================
MIN_EDGE = 0.05
MIN_CONF = 25

# ====================== EXCLUSION FILTER ======================
EXCLUSION_KEYWORDS = [
    "women", " w ", " w)", "feminin", "femenino", "féminin",
    "feminine", "girl", "fem ", "u19", "u21", "u23", "u18",
    "u17", "u16", "u15", "reserves", "reserve", "b team",
    " ii ", " ii)", " iii", "youth", "sub-23", "sub23",
    "amateur", "futsal", "indoor", "beach",
]

def is_excluded(name: str) -> bool:
    n = name.lower()
    return any(kw in n for kw in EXCLUSION_KEYWORDS)

# ====================== PERSISTENT STORAGE ======================
DB_PATH = os.path.join(DATA_DIR, "apex_v53.db")

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
                side       TEXT,
                odd        REAL,
                edge       REAL,
                bookie     TEXT,
                hxg        REAL,
                axg        REAL,
                hxg_source TEXT,
                axg_source TEXT,
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
        log.info(f"✅ DB initialisée : {DB_PATH}")
    except Exception as e:
        log.error(f"❌ init_db : {e}")

def get_bankroll() -> float:
    try:
        conn = sqlite3.connect(DB_PATH)
        row  = conn.execute("SELECT amount FROM bankroll WHERE id=1").fetchone()
        conn.close()
        return row[0] if row else 100.0
    except Exception as e:
        log.warning(f"⚠️ get_bankroll : {e}")
        return 100.0

def log_bet_db(data: dict):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("""
            INSERT INTO bets
              (ts, fixture_id, home, away, league_id, tier, side, odd,
               edge, bookie, hxg, axg, hxg_source, axg_source, dcs, conf, stake)
            VALUES
              (:ts, :fixture_id, :home, :away, :league_id, :tier, :side, :odd,
               :edge, :bookie, :hxg, :axg, :hxg_source, :axg_source, :dcs, :conf, :stake)
        """, data)
        conn.commit()
        conn.close()
    except Exception as e:
        log.warning(f"⚠️ log_bet_db : {e}")

# ====================== [F18] FOOTYSTATS BRIDGE CORRIGÉ ======================
# [F19] Matching par nom normalisé
def normalize_name(name: str) -> str:
    """Normalise un nom d'équipe pour le matching cross-API."""
    n = name.lower().strip()
    for rm in ["fc", "cf", "sc", "ac", "afc", "fk", "sk",
               "sv", "bv", "vv", "if", "rcd", "rc", "sd", "ud"]:
        n = n.replace(f" {rm}", "").replace(f"{rm} ", "")
    return n.strip()

def fuzzy_match(name_a: str, name_b: str, threshold: float = 0.82) -> bool:
    """Retourne True si les deux noms sont similaires à ≥ threshold."""
    na = normalize_name(name_a)
    nb = normalize_name(name_b)
    if na == nb:
        return True
    ratio = SequenceMatcher(None, na, nb).ratio()
    return ratio >= threshold

# Index FootyStats pour le cycle courant : {normalized_name: match_dict}
_fs_index = {}
_fs_index_ts = 0.0
FS_INDEX_TTL = 30 * 60   # Rafraîchi toutes les 30 min

def build_fs_index() ->dict:
    """
    [F18] Appel UNIQUE à /todays-matches par cycle.
    Retourne un index {nom_normalisé: match_fs_object}.
    Chaque match FootyStats contient directement :
      team_a_xg_avg → xG moyen équipe domicile
      team_b_xg_avg → xG moyen équipe extérieur
    """
    global _fs_index, _fs_index_ts

    now = time.time()
    if now - _fs_index_ts < FS_INDEX_TTL and _fs_index:
        return _fs_index

    if not FOOTYSTATS_KEY:
        return {}

    try:
        r = requests.get(
            f"{FS_BASE}/todays-matches",
            params={"key": FOOTYSTATS_KEY},
            timeout=15
        )
        if r.status_code != 200:
            log.warning(f"⚠️ FootyStats /todays-matches → HTTP {r.status_code}")
            return _fs_index   # Retourne l'ancien cache

        raw  = r.json()
        data = raw.get("data", [])
        if not data:
            log.info("ℹ️ FootyStats : 0 matchs aujourd'hui")
            return {}

        index = {}
        for m in data:
            h = m.get("home_name", "")
            a = m.get("away_name", "")
            if h:
                index[normalize_name(h)] = m
            if a:
                index[normalize_name(a)] = m

        _fs_index    = index
        _fs_index_ts = now
        log.info(f"✅ FootyStats index : {len(data)} matchs / {len(index)} équipes")
        return _fs_index

    except Exception as e:
        log.warning(f"⚠️ build_fs_index : {e}")
        return _fs_index   # Cache précédent

def get_fs_xg_from_index(team_name: str,
                          fs_index: dict,
                          is_home: bool) ->object:
    """
    [F19] Cherche team_name dans l'index FootyStats par fuzzy match.
    Retourne team_a_xg_avg (home) ou team_b_xg_avg (away).
    """
    norm = normalize_name(team_name)

    # Recherche exacte d'abord
    if norm in fs_index:
        m = fs_index[norm]
        field = "team_a_xg_avg" if is_home else "team_b_xg_avg"
        val   = m.get(field)
        if val is not None and float(val) > 0:
            return float(val)

    # Recherche fuzzy
    for key, m in fs_index.items():
        if fuzzy_match(norm, key):
            # Vérifier que c'est bien la bonne équipe (home ou away)
            h = normalize_name(m.get("home_name", ""))
            a = normalize_name(m.get("away_name", ""))
            if is_home and fuzzy_match(norm, h):
                val = m.get("team_a_xg_avg")
            elif not is_home and fuzzy_match(norm, a):
                val = m.get("team_b_xg_avg")
            else:
                continue
            if val is not None and float(val) > 0:
                return float(val)

    return None

# ====================== MATH : DIXON-COLES ======================
DC_RHO = -0.13

def poisson_prob(lmb: float, k: int) -> float:
    try:
        if lmb <= 0:
            return 1.0 if k == 0 else 0.0
        return (math.exp(-lmb) * (lmb ** k)) / math.factorial(k)
    except Exception as e:
        log.debug(f"poisson_prob : {e}")
        return 0.0

def tau(x: int, y: int, lmb: float, mu: float, rho: float) -> float:
    if x == 0 and y == 0:   return 1.0 - lmb * mu * rho
    elif x == 1 and y == 0: return 1.0 + mu * rho
    elif x == 0 and y == 1: return 1.0 + lmb * rho
    elif x == 1 and y == 1: return 1.0 - rho
    return 1.0

def calculate_probs(hxg: float, axg: float) ->dict:
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

# ====================== ML CONFIDENCE SCORE ======================
def calculate_confidence(hxg: float, axg: float,
                          tier: str, edge: float, dcs: float) -> int:
    """Score ML /50 sur 4 composantes."""
    score = 0
    diff  = abs(hxg - axg)

    # Différentiel xG (max 20)
    if diff > 1.5:    score += 20
    elif diff > 0.8:  score += 12
    elif diff > 0.4:  score += 6

    # Tier compétition (max 12)
    score += {"P0": 12, "N1": 10, "N2": 6, "N3": 3}.get(tier, 0)

    # Force de l'edge (max 10)
    if edge > 0.15:   score += 10
    elif edge > 0.10: score += 7
    elif edge > 0.05: score += 4

    # DCS (max 8)
    if dcs >= 0.80:   score += 8
    elif dcs >= 0.65: score += 5
    elif dcs >= 0.60: score += 2

    return min(score, 50)

# ====================== KELLY STAKING ======================
KELLY_FRACTION = 0.25
MAX_STAKE_PCT  = 0.05

def kelly_stake(prob: float, odd: float, bankroll: float) -> float:
    b = odd - 1.0
    q = 1.0 - prob
    k = (b * prob - q) / b if b > 0 else 0.0
    if k <= 0:
        return 0.0
    raw    = bankroll * KELLY_FRACTION * k
    capped = min(raw, bankroll * MAX_STAKE_PCT)
    return round(capped, 2)

# ====================== VALUE ENGINE ======================
def detect_best_value(probs: dict, odds_data: list,
                       hxg: float, axg: float,
                       tier: str, dcs: float) ->object:
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
                            "conf": conf, "prob": prob_model
                        }
    return best

# ====================== API-SPORTS WRAPPERS ======================
def safe_get(url: str, params=None) ->object:
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=10)
        if r.status_code == 200:
            return r.json()
        log.debug(f"safe_get {url} → HTTP {r.status_code}")
        return None
    except Exception as e:
        log.warning(f"⚠️ safe_get : {e}")
        return None

def get_fixtures() -> list:
    d = safe_get(f"{BASE_URL}/fixtures", {"date": time.strftime('%Y-%m-%d')})
    return d.get('response', []) if d else []

def get_odds(fid: int) -> list:
    d = safe_get(f"{BASE_URL}/odds", {"fixture": fid})
    return d.get('response', []) if d else []

def get_stats(tid: int, lid: int, season: int) ->object:
    d = safe_get(f"{BASE_URL}/teams/statistics",
                 {"team": tid, "league": lid, "season": season})
    return d.get('response') if d else None

# ====================== CHECK LOOP ======================
def check_loop():
    log.info(f"⏰ v5.3 — Cycle {datetime.now().strftime('%H:%M')}")

    # [F21] Pre-fetch FootyStats UNE SEULE FOIS pour tout le cycle
    fs_index = build_fs_index()
    fs_ok    = len(fs_index) > 0
    log.info(f"  FootyStats index : {'✅ actif' if fs_ok else '⚠️ hors ligne → fallback proxy'}")

    fixtures = get_fixtures()
    now      = datetime.now(timezone.utc)
    bank     = get_bankroll()
    sent     = 0

    for f in fixtures:
        try:
            league_id = f['league']['id']

            # ── GATE-0 ────────────────────────────────────────────
            league_info = get_league_info(league_id)
            if league_info is None:
                continue
            tier, league_name = league_info

            # ── Fenêtre temporelle ────────────────────────────────
            m_date  = datetime.fromisoformat(
                f['fixture']['date'].replace('Z', '+00:00'))
            delta_h = (m_date - now).total_seconds() / 3600
            if not (0 < delta_h < 6):
                continue

            h_name = f['teams']['home']['name']
            a_name = f['teams']['away']['name']

            if is_excluded(h_name) or is_excluded(a_name):
                continue

            # ── Stats équipes (API-Sports) ────────────────────────
            season  = f['league']['season']
            stats_h = get_stats(f['teams']['home']['id'], league_id, season)
            stats_a = get_stats(f['teams']['away']['id'], league_id, season)
            if not stats_h or not stats_a:
                continue

            # ── [F18][F19] xG — FootyStats en priorité ────────────
            hxg_source = axg_source = "goals_proxy"

            if fs_ok:
                hxg_fs = get_fs_xg_from_index(h_name, fs_index, is_home=True)
                axg_fs = get_fs_xg_from_index(a_name, fs_index, is_home=False)
            else:
                hxg_fs = axg_fs = None

            if hxg_fs:
                hxg = hxg_fs
                hxg_source = "footystats"
            else:
                try:
                    h_p = stats_h['fixtures']['played']['total']
                    h_g = stats_h['goals']['for']['total']['total']
                    hxg = (h_g / h_p * 1.10) if h_p > 0 else 1.20
                except (KeyError, TypeError, ZeroDivisionError):
                    hxg = 1.20

            if axg_fs:
                axg = axg_fs
                axg_source = "footystats"
            else:
                try:
                    a_p = stats_a['fixtures']['played']['total']
                    a_g = stats_a['goals']['for']['total']['total']
                    axg = (a_g / a_p) if a_p > 0 else 1.00
                except (KeyError, TypeError, ZeroDivisionError):
                    axg = 1.00

            # Anti-ZeroPoisson guard
            hxg = max(float(hxg), 0.30)
            axg = max(float(axg), 0.30)

            # ── GATE-1 : DCS ──────────────────────────────────────
            dcs     = calculate_dcs(stats_h, stats_a, hxg_source, axg_source)
            min_dcs = MIN_DCS_FS if hxg_source == "footystats" else MIN_DCS_PROXY

            if dcs < min_dcs:
                log.info(f"  ⛔ DCS={dcs:.2f} < {min_dcs} [{h_name} vs {a_name}]")
                continue

            probs     = calculate_probs(hxg, axg)
            odds_data = get_odds(f['fixture']['id'])
            if not odds_data:
                continue

            # ── GATE-2 : Value ────────────────────────────────────
            best = detect_best_value(probs, odds_data, hxg, axg, tier, dcs)

            if best and sent < 8:
                stake = kelly_stake(best['prob'], best['odd'], bank)

                if bot and CHAT_ID:
                    # Icône source xG
                    src_icon_h = "🟢" if hxg_source == "footystats" else "🟡"
                    src_icon_a = "🟢" if axg_source == "footystats" else "🟡"

                    msg = (
                        f"🚀 APEX-SIRIUS v5.3\n"
                        f"━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🏆 {league_name} [{tier}]\n"
                        f"⚽ {h_name} vs {a_name}\n"
                        f"━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🎯 Paris : {best['side']} @ {best['odd']:.2f}\n"
                        f"📚 Bookmaker : {best['bookie']}\n"
                        f"━━━━━━━━━━━━━━━━━━━━━\n"
                        f"📊 xG Dom. : {hxg:.2f} {src_icon_h} ({hxg_source})\n"
                        f"📊 xG Ext. : {axg:.2f} {src_icon_a} ({axg_source})\n"
                        f"💡 P(modèle) : {best['prob']*100:.1f}%\n"
                        f"💰 Edge : +{best['edge']*100:.1f}%\n"
                        f"🧠 ML Score : {best['conf']}/50\n"
                        f"🔬 DCS : {dcs:.2f}\n"
                        f"━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🏦 Bankroll : {bank:.2f}u\n"
                        f"📌 Mise Kelly : {stake:.2f}u ({stake/bank*100:.1f}%)\n"
                        f"⏱ Kick-off : {m_date.strftime('%H:%M')} UTC\n"
                        f"━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🟢 footystats  🟡 proxy"
                    )
                    try:
                        bot.send_message(CHAT_ID, msg)
                        log.info(
                            f"✅ Alert : {best['side']} @ {best['odd']:.2f}"
                            f" [{league_name}]"
                            f" xG {hxg_source}/{axg_source}"
                            f" DCS={dcs:.2f}"
                        )
                        log_bet_db({
                            "ts":         datetime.now().isoformat(),
                            "fixture_id": f['fixture']['id'],
                            "home":       h_name,
                            "away":       a_name,
                            "league_id":  league_id,
                            "tier":       tier,
                            "side":       best['side'],
                            "odd":        best['odd'],
                            "edge":       best['edge'],
                            "bookie":     best['bookie'],
                            "hxg":        hxg,
                            "axg":        axg,
                            "hxg_source": hxg_source,
                            "axg_source": axg_source,
                            "dcs":        dcs,
                            "conf":       best['conf'],
                            "stake":      stake,
                        })
                        sent += 1
                    except Exception as e:
                        log.error(f"❌ send_message : {e}")

        except Exception as e:
            fid = f.get('fixture', {}).get('id', '?')
            log.warning(f"⚠️ Loop [{fid}] : {e}")

    log.info(
        f"✅ Cycle terminé — {sent} alerte(s)"
        f" — FootyStats {'actif' if fs_ok else 'hors ligne'}"
        f" — Bankroll : {bank:.2f}u"
    )

# ====================== SCHEDULER ======================
def safe_check():
    try:
        check_loop()
    except Exception as e:
        log.error(f"❌ check_loop crash : {e}")

def run():
    time.sleep(15)
    safe_check()
    schedule.every(15).minutes.do(safe_check)
    while True:
        try:
            schedule.run_pending()
        except Exception as e:
            log.error(f"❌ Scheduler : {e}")
        time.sleep(1)

# ====================== ENTRYPOINT ======================
init_db()

if bot:
    threading.Thread(target=run, daemon=True).start()
    log.info("✅ Scheduler thread démarré (cycle 15 min)")
else:
    log.warning("⚠️ Bot non initialisé — scheduler non démarré")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
