#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════╗
║          APEX-SIRIUS v5.2 — HARDENED EDITION                ║
║──────────────────────────────────────────────────────────────║
║  Fixes vs v5.1:                                              ║
║  [F1]  CHAT_ID casté en int                                  ║
║  [F2]  Guard bot/CHAT_ID avant send_message                  ║
║  [F3]  Anti-ZeroPoisson guard (hxg/axg min 0.30)            ║
║  [F4]  Storage persistant SQLite (bankroll + bets)           ║
║  [F5]  Gate-0 : Whitelist leagues + mapping tier réel        ║
║  [F6]  Gate-1 : DCS (Data Confidence Score) avec seuil       ║
║  [F7]  Gate-2 : Edge minimum + conf minimum                  ║
║  [F8]  calculate_confidence rebasé sur 50 pts réels          ║
║  [F9]  Dixon-Coles τ(x,y,λ,μ,ρ) calibré (rho=-0.13)        ║
║  [F10] Filtres detect_best_value corrigés (logique cohérente)║
║  [F11] Cache FootyStats avec TTL 6h                          ║
║  [F12] Filtre exclusion étendu (u19→u23, futsal, fem, etc.)  ║
║  [F13] Kelly staking intégré dans les alertes                ║
║  [F14] Logging structuré (plus d'except: pass silencieux)    ║
║  [F15] Scheduler avec recovery wrapper                       ║
║  [F16] FOOTYSTATS_KEY en param (pas dans l'URL)              ║
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

# ====================== LOGGING ======================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("APEX")
log.info("🚀 APEX-SIRIUS v5.2 - HARDENED EDITION")

# ====================== FLASK ======================
app = Flask(__name__)

@app.route('/')
def home():
    return "🤖 APEX-SIRIUS v5.2 Running", 200

@app.route('/ping')
def ping():
    return "pong", 200

# ====================== CONFIG ======================
BOT_TOKEN       = os.environ.get("BOT_TOKEN")
CHAT_ID_RAW     = os.environ.get("CHAT_ID")
API_KEY         = os.environ.get("API_KEY")
FOOTYSTATS_KEY  = os.environ.get("FOOTYSTATS_KEY")

# DATA_DIR : monter un volume persistant sur Render/Railway et pointer ici
# ex: DATA_DIR=/data  (Render Disk) ou /var/data  (Railway Volume)
DATA_DIR = os.environ.get("DATA_DIR", "/tmp")

# [F1] CHAT_ID casté en int — os.environ retourne un str
try:
    CHAT_ID = int(CHAT_ID_RAW) if CHAT_ID_RAW else None
except ValueError:
    CHAT_ID = None
    log.error("❌ CHAT_ID invalide : doit être un entier")

bot = None
_required = {"BOT_TOKEN": BOT_TOKEN, "CHAT_ID": CHAT_ID,
             "API_KEY": API_KEY, "FOOTYSTATS_KEY": FOOTYSTATS_KEY}
_missing = [k for k, v in _required.items() if not v]

if _missing:
    log.error(f"❌ Variables manquantes : {', '.join(_missing)}")
else:
    try:
        bot = telebot.TeleBot(BOT_TOKEN)
        log.info("✅ Telegram Bot initialisé")
    except Exception as e:
        log.error(f"❌ Erreur init Telegram : {e}")

BASE_URL = "https://v3.football.api-sports.io"
HEADERS  = {"x-apisports-key": API_KEY} if API_KEY else {}
FS_URL   = "https://api.footystats.org/v2"

# ====================== GATE-0 : WHITELIST + TIER MAP ======================
# [F5] Mapping league_id (API-Sports) → (tier, nom)
# Seuls ces leagues génèrent des alertes.
LEAGUE_WHITELIST: dict[int, tuple[str, str]] = {
    # ── P0 : Compétitions UEFA ──────────────────────────────
    2:   ("P0", "UEFA Champions League"),
    3:   ("P0", "UEFA Europa League"),
    848: ("P0", "UEFA Conference League"),
    # ── N1 : Top 5 Leagues ──────────────────────────────────
    39:  ("N1", "Premier League"),
    140: ("N1", "La Liga"),
    78:  ("N1", "Bundesliga"),
    135: ("N1", "Serie A"),
    61:  ("N1", "Ligue 1"),
    # ── N2 : Ligues fortes ──────────────────────────────────
    94:  ("N2", "Primeira Liga"),
    88:  ("N2", "Eredivisie"),
    144: ("N2", "Pro League Belgique"),
    203: ("N2", "Süper Lig"),
    119: ("N2", "Superliga Danemark"),
    113: ("N2", "Allsvenskan"),
    # ── N3 : Surveillées ────────────────────────────────────
    71:  ("N3", "Série A Brésil"),
    128: ("N3", "Liga Profesional Argentine"),
    188: ("N3", "MLS"),
    253: ("N3", "Saudi Pro League"),
}

def get_league_info(league_id: int) -> tuple[str, str] | None:
    """Retourne (tier, nom) ou None si hors whitelist."""
    return LEAGUE_WHITELIST.get(league_id)

# ====================== GATE-1 : DCS ======================
# [F6] Data Confidence Score [0.0 – 1.0]
MIN_DCS = 0.60  # Seuil minimum pour générer une alerte

def calculate_dcs(stats_h: dict, stats_a: dict,
                  hxg_source: str, axg_source: str) -> float:
    """
    Facteurs :
      - Matchs joués par les deux équipes   → max 0.40
      - Qualité source xG (footystats > proxy)→ max 0.40
      - Complétude des stats goals           → max 0.20
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

    # Source xG
    score += 0.20 if hxg_source == "footystats" else 0.08
    score += 0.20 if axg_source == "footystats" else 0.08

    # Complétude goals
    try:
        _ = stats_h['goals']['for']['total']['total']
        _ = stats_a['goals']['for']['total']['total']
        score += 0.20
    except (KeyError, TypeError):
        pass

    return min(round(score, 3), 1.0)

# ====================== GATE-2 : VALUE & CONF ======================
# [F7] Seuils minimaux Gate-2
MIN_EDGE = 0.05   # 5 % d'edge minimum
MIN_CONF = 25     # Score ML minimum

# ====================== EXCLUSION FILTER ======================
# [F12] Mots-clés étendus : U19→U23, futsal, féminines, réserves…
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

# ====================== PERSISTENT STORAGE (SQLite) ======================
# [F4] Plus de /tmp volatile : SQLite sur DATA_DIR configurable
DB_PATH = os.path.join(DATA_DIR, "apex_v52.db")

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
                dcs        REAL,
                conf       INTEGER,
                stake      REAL,
                result     TEXT DEFAULT 'PENDING',
                pnl        REAL DEFAULT 0.0
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS bankroll (
                id     INTEGER PRIMARY KEY CHECK (id = 1),
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

def update_bankroll(amount: float):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("UPDATE bankroll SET amount=? WHERE id=1", (amount,))
        conn.commit()
        conn.close()
    except Exception as e:
        log.warning(f"⚠️ update_bankroll : {e}")

def log_bet_db(data: dict):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("""
            INSERT INTO bets
              (ts, fixture_id, home, away, league_id, tier, side, odd,
               edge, bookie, hxg, axg, dcs, conf, stake)
            VALUES
              (:ts, :fixture_id, :home, :away, :league_id, :tier, :side, :odd,
               :edge, :bookie, :hxg, :axg, :dcs, :conf, :stake)
        """, data)
        conn.commit()
        conn.close()
    except Exception as e:
        log.warning(f"⚠️ log_bet_db : {e}")

# ====================== FOOTYSTATS BRIDGE ======================
# [F11] Cache avec TTL 6h  [F16] Clé en param, pas dans l'URL
FS_CACHE: dict[str, tuple[float, float]] = {}   # {nom: (xg, timestamp)}
FS_CACHE_TTL = 6 * 3600                          # 6 heures

def get_fs_xg(team_name: str) -> float | None:
    """Fetch xG FootyStats avec cache TTL 6h."""
    now = time.time()
    if team_name in FS_CACHE:
        val, ts = FS_CACHE[team_name]
        if now - ts < FS_CACHE_TTL:
            return val

    if not FOOTYSTATS_KEY:
        return None

    try:
        # [F16] Clé en query param — pas exposée dans le chemin URL
        r = requests.get(
            f"{FS_URL}/teams",
            params={"key": FOOTYSTATS_KEY, "search": team_name},
            timeout=7
        )
        if r.status_code != 200:
            log.debug(f"FS search {team_name} → {r.status_code}")
            return None

        data = r.json().get('data', [])
        if not data:
            return None

        tid = data[0].get('id')
        if not tid:
            return None

        r2 = requests.get(
            f"{FS_URL}/team-stats",
            params={"key": FOOTYSTATS_KEY, "team_id": tid},
            timeout=7
        )
        if r2.status_code != 200:
            return None

        stats  = r2.json().get('data', {})
        xg_val = stats.get('xg_for_avg') or stats.get('total_xG')
        if xg_val is not None:
            val = float(xg_val)
            FS_CACHE[team_name] = (val, now)
            log.debug(f"  FS xG [{team_name}] = {val:.2f}")
            return val

    except Exception as e:
        log.warning(f"⚠️ get_fs_xg({team_name}) : {e}")

    return None

# ====================== MATH : DIXON-COLES POISSON ======================
# [F9] Correction Dixon-Coles avec ρ calibré (rho = -0.13, D&C 1997)
DC_RHO = -0.13

def poisson_prob(lmb: float, k: int) -> float:
    try:
        if lmb <= 0:
            return 1.0 if k == 0 else 0.0
        return (math.exp(-lmb) * (lmb ** k)) / math.factorial(k)
    except Exception as e:
        log.debug(f"poisson_prob err : {e}")
        return 0.0

def tau(x: int, y: int, lmb: float, mu: float, rho: float) -> float:
    """
    Facteur de correction Dixon-Coles pour les faibles scores.
    τ(x,y,λ,μ,ρ) — appliqué uniquement pour x,y ∈ {0,1}.
    """
    if x == 0 and y == 0:   return 1.0 - lmb * mu * rho
    elif x == 1 and y == 0: return 1.0 + mu * rho
    elif x == 0 and y == 1: return 1.0 + lmb * rho
    elif x == 1 and y == 1: return 1.0 - rho
    return 1.0

def calculate_probs(hxg: float, axg: float) -> dict[str, float]:
    """
    Modèle Poisson bivariée avec correction Dixon-Coles.
    Boucle sur 0–6 buts par équipe (99 %+ de la masse).
    Probabilités normalisées en sortie.
    """
    probs = {"H": 0.0, "D": 0.0, "A": 0.0}
    hp = [poisson_prob(hxg, i) for i in range(7)]
    ap = [poisson_prob(axg, i) for i in range(7)]

    for h in range(7):
        for a in range(7):
            t = tau(h, a, hxg, axg, DC_RHO)
            p = max(hp[h] * ap[a] * t, 0.0)   # Guard contre valeur négative
            if   h > a: probs["H"] += p
            elif h == a: probs["D"] += p
            else:        probs["A"] += p

    total = sum(probs.values())
    if total > 0:
        probs = {k: v / total for k, v in probs.items()}
    return probs

# ====================== ML CONFIDENCE SCORE ======================
# [F8] Score rebasé [0–50], distribué sur 4 facteurs réels
def calculate_confidence(hxg: float, axg: float,
                          tier: str, edge: float, dcs: float) -> int:
    """
    Score ML /50 — 4 composantes :
      • Différentiel xG   → max 20 pts
      • Tier compétition  → max 12 pts  (variable selon whitelist, pas hardcodé)
      • Force de l'edge   → max 10 pts
      • DCS               → max  8 pts
    """
    score = 0
    diff  = abs(hxg - axg)

    # Différentiel xG (max 20)
    if diff > 1.5:    score += 20
    elif diff > 0.8:  score += 12
    elif diff > 0.4:  score += 6

    # Tier (max 12) — [F5] tier vient du whitelist, pas hardcodé "N1"
    score += {"P0": 12, "N1": 10, "N2": 6, "N3": 3}.get(tier, 0)

    # Edge (max 10)
    if edge > 0.15:   score += 10
    elif edge > 0.10: score += 7
    elif edge > 0.05: score += 4

    # DCS (max 8)
    if dcs >= 0.80:   score += 8
    elif dcs >= 0.65: score += 5
    elif dcs >= 0.60: score += 2

    return min(score, 50)

# ====================== KELLY STAKING ======================
# [F13] Kelly fractionnel intégré
KELLY_FRACTION  = 0.25   # Kelly 1/4
MAX_STAKE_PCT   = 0.05   # Plafond 5 % du bankroll par bet

def kelly_stake(prob: float, odd: float, bankroll: float) -> float:
    b = odd - 1.0
    q = 1.0 - prob
    k = (b * prob - q) / b if b > 0 else 0.0
    if k <= 0:
        return 0.0
    raw   = bankroll * KELLY_FRACTION * k
    capped = min(raw, bankroll * MAX_STAKE_PCT)
    return round(capped, 2)

# ====================== VALUE ENGINE ======================
# [F10] Filtres logiquement cohérents
def detect_best_value(probs: dict, odds_data: list,
                       hxg: float, axg: float,
                       tier: str, dcs: float) -> dict | None:
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
                    implied    = 1.0 / odd
                    edge       = prob_model - implied

                    # Gate-2 : edge minimum global
                    if edge < MIN_EDGE:
                        continue

                    # [F10] Filtres cohérents et non-contradictoires :
                    # Draw : bar plus élevé (volatilité haute)
                    if key == "D" and edge < 0.08:
                        continue
                    # Away : modèle doit soutenir la sélection (P≥30%)
                    if key == "A" and prob_model < 0.30:
                        continue
                    # Home : on n'élimine pas une value bet potentielle ;
                    # on filtre uniquement quand le modèle est très défavorable
                    # ET la cote est très serrée (signal contraire fort)
                    if key == "H" and prob_model < 0.35 and odd < 1.60:
                        continue

                    conf = calculate_confidence(hxg, axg, tier, edge, dcs)

                    # Gate-2b : conf minimum
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

# ====================== API WRAPPERS ======================
def safe_get(url: str, params: dict | None = None) -> dict | None:
    # [F14] Plus d'except silencieux — on log toujours
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=10)
        if r.status_code == 200:
            return r.json()
        log.debug(f"safe_get {url} → HTTP {r.status_code}")
        return None
    except Exception as e:
        log.warning(f"⚠️ safe_get({url}) : {e}")
        return None

def get_fixtures() -> list:
    d = safe_get(f"{BASE_URL}/fixtures", {"date": time.strftime('%Y-%m-%d')})
    return d.get('response', []) if d else []

def get_odds(fid: int) -> list:
    d = safe_get(f"{BASE_URL}/odds", {"fixture": fid})
    return d.get('response', []) if d else []

def get_stats(tid: int, lid: int, season: int) -> dict | None:
    d = safe_get(f"{BASE_URL}/teams/statistics",
                 {"team": tid, "league": lid, "season": season})
    return d.get('response') if d else None

# ====================== CHECK LOOP ======================
def check_loop():
    log.info(f"⏰ v5.2 — Cycle {datetime.now().strftime('%H:%M')}")
    fixtures = get_fixtures()
    now  = datetime.now(timezone.utc)
    bank = get_bankroll()
    sent = 0

    for f in fixtures:
        try:
            league_id   = f['league']['id']

            # ── GATE-0 : Whitelist ────────────────────────────────
            league_info = get_league_info(league_id)
            if league_info is None:
                continue
            tier, league_name = league_info

            # ── Fenêtre temporelle ────────────────────────────────
            m_date  = datetime.fromisoformat(f['fixture']['date'].replace('Z', '+00:00'))
            delta_h = (m_date - now).total_seconds() / 3600
            if not (0 < delta_h < 6):
                continue

            h_name = f['teams']['home']['name']
            a_name = f['teams']['away']['name']

            # ── Exclusion étendue ─────────────────────────────────
            if is_excluded(h_name) or is_excluded(a_name):
                continue

            # ── Stats équipes ─────────────────────────────────────
            season  = f['league']['season']
            stats_h = get_stats(f['teams']['home']['id'], league_id, season)
            stats_a = get_stats(f['teams']['away']['id'], league_id, season)
            if not stats_h or not stats_a:
                continue

            # ── Hybrid xG avec tracking de source ────────────────
            hxg_source = axg_source = "goals_proxy"

            hxg = get_fs_xg(h_name)
            if hxg:
                hxg_source = "footystats"
            else:
                try:
                    h_p = stats_h['fixtures']['played']['total']
                    h_g = stats_h['goals']['for']['total']['total']
                    hxg = (h_g / h_p * 1.10) if h_p > 0 else 1.20
                except (KeyError, TypeError, ZeroDivisionError):
                    hxg = 1.20

            axg = get_fs_xg(a_name)
            if axg:
                axg_source = "footystats"
            else:
                try:
                    a_p = stats_a['fixtures']['played']['total']
                    a_g = stats_a['goals']['for']['total']['total']
                    axg = (a_g / a_p) if a_p > 0 else 1.00
                except (KeyError, TypeError, ZeroDivisionError):
                    axg = 1.00

            # [F3] Anti-ZeroPoisson : lambda minimum 0.30
            hxg = max(float(hxg), 0.30)
            axg = max(float(axg), 0.30)

            # ── GATE-1 : DCS ──────────────────────────────────────
            dcs = calculate_dcs(stats_h, stats_a, hxg_source, axg_source)
            if dcs < MIN_DCS:
                log.info(f"  ⛔ DCS={dcs:.2f} < {MIN_DCS} [{h_name} vs {a_name}]")
                continue

            probs     = calculate_probs(hxg, axg)
            odds_data = get_odds(f['fixture']['id'])
            if not odds_data:
                continue

            # ── Value Engine ──────────────────────────────────────
            best = detect_best_value(probs, odds_data, hxg, axg, tier, dcs)

            if best and sent < 8:
                stake = kelly_stake(best['prob'], best['odd'], bank)

                # [F2] Guard avant send_message
                if bot and CHAT_ID:
                    msg = (
                        f"🚀 APEX-SIRIUS v5.2\n"
                        f"━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🏆 {league_name} [{tier}]\n"
                        f"⚽ {h_name} vs {a_name}\n"
                        f"━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🎯 Paris : {best['side']} @ {best['odd']:.2f}\n"
                        f"📚 Bookmaker : {best['bookie']}\n"
                        f"━━━━━━━━━━━━━━━━━━━━━\n"
                        f"📊 xG Dom. : {hxg:.2f} ({hxg_source})\n"
                        f"📊 xG Ext. : {axg:.2f} ({axg_source})\n"
                        f"💡 P(modèle) : {best['prob']*100:.1f}%\n"
                        f"💰 Edge : +{best['edge']*100:.1f}%\n"
                        f"🧠 ML Score : {best['conf']}/50\n"
                        f"🔬 DCS : {dcs:.2f}\n"
                        f"━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🏦 Bankroll : {bank:.2f}u\n"
                        f"📌 Mise Kelly : {stake:.2f}u ({stake/bank*100:.1f}%)\n"
                        f"⏱ Kick-off : {m_date.strftime('%H:%M')} UTC"
                    )
                    try:
                        bot.send_message(CHAT_ID, msg)
                        log.info(f"✅ Alert envoyée : {best['side']} @ {best['odd']:.2f} [{league_name}]")

                        # [F4] Log persistant SQLite
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

    log.info(f"✅ Cycle terminé — {sent} alerte(s) envoyée(s) — Bankroll : {bank:.2f}u")

# ====================== SCHEDULER ======================
# [F15] Recovery wrapper autour du scheduler
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
            log.error(f"❌ Scheduler run_pending : {e}")
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
