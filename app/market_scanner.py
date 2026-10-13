# ============================================================
# MARKET SCANNER – Trading X Hyper Pro
# PRODUCCIÓN REAL – FAILSAFE
# ============================================================

from __future__ import annotations

import os
import time
import threading
from app.symbol_policy import BLOCKED_MEME_KEYWORDS, rejection_reason
from app.scan_coverage import Coverage
from app.settings_utils import env_float
from typing import Dict, List, Optional, Set

from app.config import (
    PRODUCTION_MODE,
    SCANNER_DEPTH,
    VERBOSE_LOGS,
    SCANNER_MIN_24H_NOTIONAL,
    SCANNER_MIN_OPEN_INTEREST,
    SCANNER_MAX_SPREAD_BPS,
    SCANNER_MIN_TOP_BOOK_NOTIONAL,
    SCANNER_SHORTLIST_DEPTH_FOR_L2,
    SCANNER_L2_CACHE_TTL,
)
from app.hyperliquid_client import make_request


# ============================================================
# LOG
# ============================================================

def safe_log(*args):
    if VERBOSE_LOGS or not PRODUCTION_MODE:
        print(*args)


# ============================================================
# CACHE FAILSAFE
# ============================================================

_LAST_GOOD_RESULTS: List[dict] = []
_LAST_UPDATE_TS: float = 0.0
MAX_CACHE_AGE = 300  # 5 minutos
SCANNER_HTTP_TIMEOUT_SECONDS = max(float(os.getenv("SCANNER_HTTP_TIMEOUT_SECONDS", "4.0") or 4.0), 1.0)
SCANNER_HTTP_RETRIES = max(int(os.getenv("SCANNER_HTTP_RETRIES", "2") or 2), 1)
SCANNER_HTTP_BACKOFF = max(float(os.getenv("SCANNER_HTTP_BACKOFF", "0.35") or 0.35), 0.0)

_L2_QUALITY_CACHE: dict[str, tuple[float, dict]] = {}

_scan_coverage = Coverage()
_l2_coverage = Coverage()
_scan_lock = threading.Lock()
_SCAN_REFRESH_SECONDS = env_float("SCANNER_REFRESH_SECONDS", 5.0, minimum=1.0)
_L2_BUDGET_SECONDS = env_float("SCANNER_L2_BUDGET_SECONDS", 12.0, minimum=1.0)


def mark_symbol_evaluated(symbol: str) -> None:
    _scan_coverage.mark(symbol)


# ============================================================
# ROTACIÓN / ANTI-REPETICIÓN
# ============================================================
# Evita quedarse clavado en el mismo símbolo.
RECENT_TTL_SECONDS = 15 * 60
_recent_picks: dict[str, float] = {}
_rr_index: int = 0


def _prune_recent(now: float) -> None:
    dead = [s for s, ts in _recent_picks.items() if (now - ts) > (RECENT_TTL_SECONDS * 2)]
    for s in dead:
        _recent_picks.pop(s, None)


# ============================================================
# UTIL
# ============================================================

def _as_perp_symbol(coin: str) -> str:
    c = (coin or "").strip().upper()
    if not c:
        return ""
    if c.endswith("-PERP"):
        return c
    return f"{c}-PERP"


def _base_coin(symbol: str) -> str:
    sym = str(symbol or "").strip().upper()
    if sym.endswith("-PERP"):
        sym = sym[:-5]
    return sym


def _is_blocked_meme_symbol(symbol: str) -> bool:
    base = _base_coin(symbol)
    return bool(rejection_reason(base))


# ============================================================
# OBTENER MERCADOS
# ============================================================

def _fetch_markets() -> Optional[Dict[str, dict]]:
    r = make_request("/info", {"type": "metaAndAssetCtxs"}, retries=SCANNER_HTTP_RETRIES, backoff=SCANNER_HTTP_BACKOFF, timeout=SCANNER_HTTP_TIMEOUT_SECONDS)
    if not isinstance(r, list) or len(r) < 2:
        return None

    meta, asset_ctxs = r[:2]
    if not isinstance(meta, dict) or not isinstance(meta.get("universe"), list) or not isinstance(asset_ctxs, list):
        return None
    universe = meta.get("universe", []) if isinstance(meta, dict) else []

    out: Dict[str, dict] = {}

    for i, asset in enumerate(asset_ctxs):
        if not isinstance(asset, dict):
            continue

        symbol = asset.get("coin")
        if not symbol and i < len(universe):
            symbol = universe[i].get("name")

        if not symbol:
            continue

        try:
            price = float(asset.get("markPx", 0))
            volume = float(asset.get("dayNtlVlm", 0))
        except Exception:
            continue

        if price <= 0 or volume <= 0:
            continue

        out[_as_perp_symbol(symbol)] = asset

    return out


# ============================================================
# SCORE SIMPLE (NO BLOQUEANTE)
# ============================================================

def _score_symbol(symbol: str, info: dict) -> Optional[dict]:
    try:
        price = float(info.get("markPx", 0))
        prev = float(info.get("prevDayPx", price))
        vol = float(info.get("dayNtlVlm", 0))
        oi_units = float(info.get("openInterest", 0))
    except Exception:
        return None

    if price <= 0 or vol <= 0:
        return None

    # Hyperliquid reporta openInterest en unidades del activo; para comparar
    # mercados usamos notional aproximado en USD/USDC.
    oi_notional = max(0.0, oi_units * price)
    if vol < float(SCANNER_MIN_24H_NOTIONAL):
        return None
    if oi_notional < float(SCANNER_MIN_OPEN_INTEREST):
        return None

    change = ((price - prev) / prev * 100) if prev > 0 else 0.0

    vol_score = min(vol / 25_000_000, 1.0)
    oi_score = min(oi_notional / 15_000_000, 1.0)
    # Actividad direccional neutra: un short no debe ser penalizado porque el
    # cambio diario sea negativo. Se premia magnitud, no signo.
    activity_score = min(abs(change) / 6.0, 1.0)

    score = (vol_score * 0.50) + (oi_score * 0.30) + (activity_score * 0.20)

    return {
        "symbol": symbol,
        "price": round(price, 6),
        "score": round(score, 4),
        "volume": round(vol, 2),
        "oi": round(oi_notional, 2),
        "oi_units": round(oi_units, 8),
        "change_24h": round(change, 2),
    }


def _fetch_l2_quality(symbol: str) -> Optional[dict]:
    coin = _base_coin(symbol)
    if not coin:
        return None

    now = time.time()
    cached = _L2_QUALITY_CACHE.get(coin)
    if cached and (now - float(cached[0])) <= float(SCANNER_L2_CACHE_TTL):
        return dict(cached[1])

    try:
        book = make_request(
            "/info",
            {"type": "l2Book", "coin": coin},
            retries=SCANNER_HTTP_RETRIES,
            backoff=SCANNER_HTTP_BACKOFF,
            timeout=SCANNER_HTTP_TIMEOUT_SECONDS,
        )
        levels = book.get("levels") if isinstance(book, dict) else None
        if not isinstance(levels, list) or len(levels) < 2:
            return None
        bids = levels[0] if isinstance(levels[0], list) else []
        asks = levels[1] if isinstance(levels[1], list) else []
        if not bids or not asks:
            return None

        bid0 = bids[0] if isinstance(bids[0], dict) else {}
        ask0 = asks[0] if isinstance(asks[0], dict) else {}
        best_bid = float(bid0.get("px", 0) or 0)
        best_ask = float(ask0.get("px", 0) or 0)
        bid_sz = float(bid0.get("sz", 0) or 0)
        ask_sz = float(ask0.get("sz", 0) or 0)
        if best_bid <= 0 or best_ask <= 0 or best_ask <= best_bid:
            return None

        mid = (best_bid + best_ask) / 2.0
        spread_bps = ((best_ask - best_bid) / max(mid, 1e-12)) * 10_000.0
        bid_notional = best_bid * max(bid_sz, 0.0)
        ask_notional = best_ask * max(ask_sz, 0.0)
        top_book_notional = min(bid_notional, ask_notional)
        out = {
            "best_bid": best_bid,
            "best_ask": best_ask,
            "spread_bps": spread_bps,
            "best_bid_notional": bid_notional,
            "best_ask_notional": ask_notional,
            "top_book_notional": top_book_notional,
        }
        _L2_QUALITY_CACHE[coin] = (now, dict(out))
        return out
    except Exception:
        return None


# ============================================================
# RESULTADOS SCANNER
# ============================================================

def _get_live_results(exclude_symbols: Set[str]) -> Optional[List[dict]]:
    markets = _fetch_markets()
    if markets is None:
        return None
    prefiltered: List[dict] = []

    for symbol, info in markets.items():
        if symbol in exclude_symbols:
            continue
        if _is_blocked_meme_symbol(symbol):
            continue
        parsed = _score_symbol(symbol, info)
        if parsed:
            prefiltered.append(parsed)

    if not prefiltered:
        return []

    prefiltered.sort(key=lambda x: x["score"], reverse=True)
    l2_depth = max(1, int(SCANNER_SHORTLIST_DEPTH_FOR_L2 or 1))
    inspect = _l2_coverage.order(prefiltered)[:l2_depth]
    results: List[dict] = []

    l2_started = time.monotonic()
    for index, parsed in enumerate(inspect):
        if index > 0 and time.monotonic() - l2_started >= _L2_BUDGET_SECONDS:
            break
        symbol = str(parsed.get("symbol") or "")
        _l2_coverage.mark(symbol)
        quality = _fetch_l2_quality(symbol)
        if not quality:
            continue
        spread_bps = float(quality.get("spread_bps") or 0.0)
        top_book = float(quality.get("top_book_notional") or 0.0)
        if spread_bps <= 0 or spread_bps > float(SCANNER_MAX_SPREAD_BPS):
            continue
        if top_book < float(SCANNER_MIN_TOP_BOOK_NOTIONAL):
            continue
        row = dict(parsed)
        row.update({
            "spread_bps": round(spread_bps, 3),
            "top_book_notional": round(top_book, 2),
            "best_bid": float(quality.get("best_bid") or 0.0),
            "best_ask": float(quality.get("best_ask") or 0.0),
            "liquidity_checked": True,
        })
        results.append(row)

    results.sort(key=lambda x: x["score"], reverse=True)
    if SCANNER_DEPTH and len(results) > SCANNER_DEPTH:
        results = results[:SCANNER_DEPTH]
    return results


def _refresh_scored_results(exclude_symbols: Set[str]) -> List[dict]:
    global _LAST_GOOD_RESULTS, _LAST_UPDATE_TS

    results = _get_live_results(exclude_symbols)
    if results is not None:
        _LAST_GOOD_RESULTS = results
        _LAST_UPDATE_TS = time.time()
        return results

    if _LAST_GOOD_RESULTS:
        age = time.time() - _LAST_UPDATE_TS
        if age <= MAX_CACHE_AGE:
            safe_log("⚠️ Scanner usando cache FAILSAFE")
            return [r for r in _LAST_GOOD_RESULTS if str(r.get("symbol") or "") not in exclude_symbols]

    safe_log("❌ Scanner sin mercados válidos (ni live ni cache)")
    return []


# ============================================================
# ORDENACIÓN DE CANDIDATOS
# ============================================================

def _get_scored_results(exclude_symbols: Set[str]) -> List[dict]:
    # Shared market snapshot. Account-specific exclusions are applied afterwards.
    with _scan_lock:
        if _LAST_UPDATE_TS > 0 and time.time() - _LAST_UPDATE_TS < _SCAN_REFRESH_SECONDS:
            results = list(_LAST_GOOD_RESULTS)
        else:
            results = _refresh_scored_results(set())
        return [row for row in results if row['symbol'] not in exclude_symbols]


def _ordered_candidates(results: List[dict], exclude_symbols: Set[str]) -> List[dict]:
    if not results:
        return []

    now = time.time()
    _prune_recent(now)

    filtered = [r for r in results if str(r.get("symbol") or "") not in exclude_symbols]
    if not filtered:
        return []

    n = len(filtered)
    start = _rr_index % n if n > 0 else 0
    rotated = filtered[start:] + filtered[:start]

    fresh: List[dict] = []
    recent: List[dict] = []
    for item in rotated:
        sym = str(item.get("symbol") or "")
        if not sym:
            continue
        last = _recent_picks.get(sym, 0.0)
        if (now - last) >= RECENT_TTL_SECONDS:
            fresh.append(item)
        else:
            recent.append(item)

    return _scan_coverage.order(fresh + recent)


# ============================================================
# API PRINCIPAL
# ============================================================

def get_ranked_symbols(exclude_symbols: Optional[Set[str]] = None, limit: Optional[int] = None) -> List[dict]:
    exclude_symbols = exclude_symbols or set()
    results = _get_scored_results(exclude_symbols)
    ordered = _ordered_candidates(results, exclude_symbols)

    if limit is not None and limit > 0:
        ordered = ordered[: int(limit)]

    if ordered and VERBOSE_LOGS:
        preview = ", ".join(f"{r.get('symbol')}:{r.get('score')}" for r in ordered[:5])
        safe_log(f"🔎 Scanner shortlist={preview}")

    return ordered


def mark_symbol_recent(symbol: str) -> None:
    global _rr_index

    sym = str(symbol or "").strip().upper()
    if not sym:
        return

    now = time.time()
    _prune_recent(now)
    _recent_picks[sym] = now
    _rr_index = (_rr_index + 1) % 1_000_000


# ============================================================
# COMPATIBILIDAD
# ============================================================

def get_best_symbol(exclude_symbols: Optional[Set[str]] = None) -> Optional[dict]:
    ranked = get_ranked_symbols(exclude_symbols=exclude_symbols, limit=1)
    if not ranked:
        return None
    picked = ranked[0]
    mark_symbol_recent(str(picked.get("symbol") or ""))
    if picked and VERBOSE_LOGS:
        safe_log(f"🔎 Scanner picked={picked.get('symbol')} score={picked.get('score')} (best-of-shortlist)")
    return picked
