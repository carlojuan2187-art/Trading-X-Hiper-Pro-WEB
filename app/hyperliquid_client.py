# ============================================================
# HYPERLIQUID CLIENT – Trading X Hyper Pro (PROD)
# FIX DEFINITIVO:
#  - Parse REAL de /exchange: status ok/err + response.data.statuses
#  - Diferencia NO_FILL (IocCancel) vs EXCHANGE_ERROR (MinTradeNtl, Tick, etc.)
#  - Log mínimo obligatorio en NO_FILL/ERROR aunque VERBOSE_LOGS=False
#  - Precio IOC desde L2 (best bid/ask) + slippage
#  - Min notional >= 10 USDC post-rounding
#  - + has_open_position(user_id): evitar múltiples posiciones abiertas
# ============================================================

import time
import threading
import httpx
from typing import Any, Dict, Optional, Tuple, List
from decimal import Decimal, ROUND_DOWN, ROUND_UP, InvalidOperation

# Si la posición abierta es menor que este notional, la tratamos como DUST.
# En vez de bloquear al bot, intentamos cerrarla y devolvemos False.
OPEN_POSITION_MIN_NOTIONAL_USDC = 5.0

from app.crypto_utils import PrivateKeyDecryptError
from app.config import (
    HYPER_BASE_URL,
    REQUEST_TIMEOUT,
    VERBOSE_LOGS,
    PRODUCTION_MODE,
    MIN_CAPITAL,
    LEVERAGE,
    EXECUTION_MAX_SPREAD_BPS,
    EXECUTION_BOOK_IMPACT_BPS,
    EXECUTION_MIN_BOOK_COVERAGE,
    EXECUTION_IOC_BASE_SLIPPAGE_BPS,
    EXECUTION_IOC_RETRY_STEP_BPS,
    EXECUTION_IOC_MAX_SLIPPAGE_BPS,
    EXECUTION_EXIT_IOC_BASE_SLIPPAGE_BPS,
    EXECUTION_EXIT_IOC_MAX_SLIPPAGE_BPS,
)

from app.database import (
    get_user_wallet,
    get_user_private_key,
)

# ------------------------------------------------------------
# Logging
# ------------------------------------------------------------

def safe_log(*args):
    if VERBOSE_LOGS or not PRODUCTION_MODE:
        print(*args)

def must_log(*args):
    # Esto SIEMPRE loguea (solo para NO_FILL / EXCHANGE_ERROR)
    print(*args)

# ------------------------------------------------------------
# HTTP Client reutilizable
# ------------------------------------------------------------

_DEFAULT_HEADERS = {"Content-Type": "application/json"}
_http_lock = threading.Lock()
_http_client: Optional[httpx.Client] = None

def _get_http_client(timeout: float) -> httpx.Client:
    global _http_client
    with _http_lock:
        if _http_client is None:
            _http_client = httpx.Client(
                timeout=timeout,
                headers=_DEFAULT_HEADERS,
                limits=httpx.Limits(max_keepalive_connections=20, max_connections=50),
            )
        return _http_client

def make_request(
    endpoint: str,
    payload: dict,
    retries: int = 4,
    backoff: float = 1.0,
    timeout: Optional[float] = None
):
    if timeout is None:
        timeout = REQUEST_TIMEOUT

    url = f"{HYPER_BASE_URL}{endpoint}"
    client = _get_http_client(timeout)

    for attempt in range(1, retries + 1):
        try:
            r = client.post(url, json=payload, timeout=timeout)

            if r.status_code == 429 or 500 <= r.status_code <= 599:
                body = r.text if hasattr(r, "text") else "<no text>"
                safe_log(f"❌ HTTP {r.status_code} {endpoint} (attempt {attempt}/{retries}) body={body}")
                if attempt < retries:
                    time.sleep(max(0.25, backoff) * (attempt * 1.5))
                    continue
                return {"_http_error": True, "_http_status": r.status_code, "_http_body": body}

            if r.status_code >= 400:
                body = r.text if hasattr(r, "text") else "<no text>"
                safe_log(f"❌ HTTP {r.status_code} {endpoint} body={body}")
                return {"_http_error": True, "_http_status": r.status_code, "_http_body": body}

            data = r.json()
            if isinstance(data, (dict, list)):
                return data
            return {"_http_error": True, "_http_status": 0, "_http_body": f"bad_json:{type(data)}"}

        except Exception as e:
            safe_log(f"❌ HTTP exception {endpoint} attempt {attempt}/{retries}:", str(e))
            if attempt < retries:
                time.sleep(backoff * attempt)

    return {"_http_error": True, "_http_status": 0, "_http_body": "request_failed"}

# ------------------------------------------------------------
# Normalización de símbolo
# ------------------------------------------------------------

def norm_coin(symbol: str) -> str:
    if not isinstance(symbol, str):
        return ""
    s = symbol.strip().upper()
    s = s.replace("-PERP", "").replace("_PERP", "").replace("PERP", "")
    if "/" in s:
        s = s.split("/", 1)[0].strip()
    return s

# ------------------------------------------------------------
# Cache meta + mids
# ------------------------------------------------------------

_META_CACHE: Dict[str, Any] = {"coin_to_asset": {}, "asset_to_sz": {}, "asset_to_tick": {}, "ts": 0.0}
_MIDS_CACHE: Dict[str, Any] = {"mids": {}, "ts": 0.0}

META_TTL = 60.0
MIDS_TTL = 2.0
_cache_lock = threading.Lock()

def _refresh_meta_cache():
    now = time.time()
    with _cache_lock:
        if now - _META_CACHE["ts"] < META_TTL:
            return

    r = make_request("/info", {"type": "meta"})
    if not isinstance(r, dict) or "universe" not in r:
        safe_log("❌ meta inválida:", r)
        return

    coin_to_asset: Dict[str, int] = {}
    asset_to_sz: Dict[int, int] = {}
    asset_to_tick: Dict[int, float] = {}

    try:
        universe = r.get("universe") if isinstance(r.get("universe"), list) else []
        for i, item in enumerate(universe):
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if name:
                coin_to_asset[str(name).upper()] = i
            try:
                szd = int(item.get("szDecimals", 0))
            except Exception:
                szd = 0
            asset_to_sz[i] = max(szd, 0)            # Tick size: el precio debe ser múltiplo del tick.
            # En meta de Hyperliquid suele venir como tickSz / tickSize (string o número).
            # ⚠️ Importante: NO confundir con tickDecimals (entero), que NO es tick size.
            tick_val = 0.0
            try:
                for k, v in item.items():
                    lk = str(k).lower()
                    if lk in ("ticksz", "ticksize"):
                        try:
                            tv = float(v)
                            if tv > 0:
                                tick_val = tv
                                break
                        except Exception:
                            continue
                asset_to_tick[i] = float(tick_val) if tick_val > 0 else 0.0
            except Exception:
                asset_to_tick[i] = 0.0

        with _cache_lock:
            _META_CACHE["coin_to_asset"] = coin_to_asset
            _META_CACHE["asset_to_sz"] = asset_to_sz
            _META_CACHE["asset_to_tick"] = asset_to_tick
            _META_CACHE["ts"] = now
    except Exception as e:
        safe_log("❌ Error meta:", str(e))

def get_asset_index(symbol: str) -> Optional[int]:
    _refresh_meta_cache()
    coin = norm_coin(symbol)
    with _cache_lock:
        return _META_CACHE["coin_to_asset"].get(coin)

def get_sz_decimals(asset_index: int) -> int:
    _refresh_meta_cache()
    with _cache_lock:
        return int(_META_CACHE["asset_to_sz"].get(asset_index, 0) or 0)

def get_tick_size(asset_index: int) -> float:
    """Tick size (paso de precio) del asset. Si no está disponible, devuelve 0.0."""
    _refresh_meta_cache()
    with _cache_lock:
        try:
            return float(_META_CACHE["asset_to_tick"].get(asset_index, 0.0) or 0.0)
        except Exception:
            return 0.0

def _refresh_mids_cache():
    now = time.time()
    with _cache_lock:
        if now - _MIDS_CACHE["ts"] < MIDS_TTL:
            return

    r = make_request("/info", {"type": "allMids"})
    if not isinstance(r, dict):
        safe_log("❌ allMids inválido:", r)
        return

    mids: Dict[str, float] = {}
    for k, v in r.items():
        if not isinstance(k, str) or k.startswith("@"):
            continue
        try:
            mids[k.upper()] = float(v)
        except Exception:
            continue

    with _cache_lock:
        _MIDS_CACHE["mids"] = mids
        _MIDS_CACHE["ts"] = now

def get_price(symbol: str) -> float:
    coin = norm_coin(symbol)
    if not coin:
        return 0.0
    _refresh_mids_cache()
    with _cache_lock:
        if time.time() - float(_MIDS_CACHE["ts"]) > max(float(MIDS_TTL) * 2, 5.0):
            return 0.0
        px = _MIDS_CACHE["mids"].get(coin)
    return float(px) if px else 0.0

# ------------------------------------------------------------
# L2 Book -> best bid/ask
# ------------------------------------------------------------

def _extract_oid_from_open_order(order: Any) -> Optional[int]:
    if not isinstance(order, dict):
        return None
    for key in ("oid", "orderId", "order_id", "o"):
        try:
            val = order.get(key)
            if val is None:
                continue
            return int(str(val).strip())
        except Exception:
            continue
    return None


def get_frontend_open_orders(user_id: int) -> List[dict]:
    wallet = get_user_wallet(user_id)
    if not wallet:
        return []
    r = make_request("/info", {"type": "frontendOpenOrders", "user": wallet})
    if isinstance(r, list):
        return [x for x in r if isinstance(x, dict)]
    return []


def get_open_orders(user_id: int) -> List[dict]:
    wallet = get_user_wallet(user_id)
    if not wallet:
        return []
    r = make_request("/info", {"type": "openOrders", "user": wallet})
    if isinstance(r, list):
        return [x for x in r if isinstance(x, dict)]
    return []


def cancel_orders(
    user_id: int,
    symbol: str,
    order_ids: List[int],
    vault_address: Optional[str] = None,
):
    wallet = get_user_wallet(user_id)
    try:
        private_key = get_user_private_key(user_id)
    except PrivateKeyDecryptError:
        return {"ok": False, "reason": "PRIVATE_KEY_DECRYPT_ERROR"}
    if not wallet or not private_key:
        return {"ok": False, "reason": "NO_WALLET_OR_KEY"}

    coin = norm_coin(symbol)
    asset = get_asset_index(coin)
    if asset is None:
        return {"ok": False, "reason": "NO_ASSET", "coin": coin}

    normalized_ids: List[int] = []
    for oid in order_ids or []:
        try:
            normalized_ids.append(int(oid))
        except Exception:
            continue
    if not normalized_ids:
        return {"ok": True, "reason": "NO_ORDER_IDS", "coin": coin, "cancelled": []}

    nonce = int(time.time() * 1000)
    expires_after_ms = nonce + 60_000
    action = {
        "type": "cancel",
        "cancels": [{"a": asset, "o": int(oid)} for oid in normalized_ids],
    }

    try:
        signer = HyperliquidSigner(private_key)
        signature = signer.sign(
            action,
            nonce,
            vault_address=vault_address,
            expires_after_ms=expires_after_ms,
        )
    except Exception as e:
        return {"ok": False, "reason": "SIGN_ERROR", "coin": coin, "error": str(e)}

    payload = {"action": action, "nonce": nonce, "signature": signature, "expiresAfter": expires_after_ms}
    if vault_address:
        payload["vaultAddress"] = vault_address

    r = make_request("/exchange", payload)
    st, inner = _unwrap_exchange(r)
    if st == "err":
        return {"ok": False, "reason": "EXCHANGE_ERR", "coin": coin, "raw": r}

    statuses = _extract_statuses(r)
    cancelled: List[int] = []
    errors: List[str] = []

    if statuses:
        for idx, s in enumerate(statuses):
            parsed = _parse_status(s)
            oid = normalized_ids[idx] if idx < len(normalized_ids) else None
            if parsed.get("kind") == "error":
                errors.append(f"{oid}:{parsed.get('error', '')}")
            else:
                if oid is not None:
                    cancelled.append(int(oid))
        return {
            "ok": len(errors) == 0,
            "reason": "CANCELLED" if len(errors) == 0 else "PARTIAL_CANCEL",
            "coin": coin,
            "cancelled": cancelled,
            "errors": errors,
            "raw": r,
        }

    if isinstance(inner, dict):
        return {"ok": True, "reason": "CANCELLED", "coin": coin, "cancelled": normalized_ids, "raw": r}

    return {"ok": False, "reason": "NO_CANCEL_STATUSES", "coin": coin, "raw": r}


def _get_l2_book(coin: str) -> Optional[dict]:
    coin = norm_coin(coin)
    if not coin:
        return None
    r = make_request("/info", {"type": "l2Book", "coin": coin})
    return r if isinstance(r, dict) else None

def _get_best_bid_ask(coin: str) -> Tuple[float, float]:
    try:
        book = _get_l2_book(coin)
        if not isinstance(book, dict):
            return (0.0, 0.0)
        levels = book.get("levels")
        if not isinstance(levels, list) or len(levels) < 2:
            return (0.0, 0.0)
        bids = levels[0] if isinstance(levels[0], list) else []
        asks = levels[1] if isinstance(levels[1], list) else []
        best_bid = float(bids[0].get("px", 0) or 0) if bids and isinstance(bids[0], dict) else 0.0
        best_ask = float(asks[0].get("px", 0) or 0) if asks and isinstance(asks[0], dict) else 0.0
        return (best_bid, best_ask)
    except Exception:
        return (0.0, 0.0)

# ✅ Export público (si algún módulo lo importa)
def get_best_bid_ask(symbol: str) -> Tuple[float, float]:
    return _get_best_bid_ask(symbol)


def _get_execution_book_metrics(coin: str, *, is_buy: bool, impact_bps: float) -> Optional[dict]:
    """Devuelve spread y profundidad ejecutable dentro de una banda de impacto.

    Para BUY mide asks; para SELL mide bids. Esto permite rechazar aperturas que
    requerirían atravesar demasiado el libro antes de enviar la IOC.
    """
    try:
        book = _get_l2_book(coin)
        levels = book.get("levels") if isinstance(book, dict) else None
        if not isinstance(levels, list) or len(levels) < 2:
            return None
        bids = levels[0] if isinstance(levels[0], list) else []
        asks = levels[1] if isinstance(levels[1], list) else []
        if not bids or not asks:
            return None
        best_bid = float((bids[0] or {}).get("px", 0) or 0)
        best_ask = float((asks[0] or {}).get("px", 0) or 0)
        if best_bid <= 0 or best_ask <= 0 or best_ask <= best_bid:
            return None
        mid = (best_bid + best_ask) / 2.0
        spread_bps = ((best_ask - best_bid) / max(mid, 1e-12)) * 10_000.0
        side_levels = asks if is_buy else bids
        best_side = best_ask if is_buy else best_bid
        impact_frac = max(float(impact_bps or 0.0), 0.0) / 10_000.0
        worst_px = best_side * (1.0 + impact_frac) if is_buy else best_side * (1.0 - impact_frac)
        depth_notional = 0.0
        top_notional = 0.0
        for idx, level in enumerate(side_levels):
            if not isinstance(level, dict):
                continue
            px = float(level.get("px", 0) or 0)
            sz = float(level.get("sz", 0) or 0)
            if px <= 0 or sz <= 0:
                continue
            if is_buy and px > worst_px:
                break
            if (not is_buy) and px < worst_px:
                break
            ntl = px * sz
            depth_notional += ntl
            if idx == 0:
                top_notional = ntl
        return {
            "best_bid": best_bid,
            "best_ask": best_ask,
            "spread_bps": spread_bps,
            "depth_notional": depth_notional,
            "top_notional": top_notional,
            "impact_bps": float(impact_bps or 0.0),
        }
    except Exception:
        return None

# ------------------------------------------------------------
# Formatting
# ------------------------------------------------------------

def _strip_trailing_zeros(num_str: str) -> str:
    if "." not in num_str:
        return num_str
    num_str = num_str.rstrip("0").rstrip(".")
    return num_str if num_str else "0"

def _to_decimal(x: float) -> Decimal:
    return Decimal(str(x))

def _quant(sz_decimals: int) -> Decimal:
    return Decimal("1") if sz_decimals <= 0 else Decimal("1").scaleb(-sz_decimals)

def _format_size_round(sz: float, sz_decimals: int, rounding) -> str:
    try:
        d = _to_decimal(sz)
        q = _quant(sz_decimals)
        out = d.quantize(q, rounding=rounding)
        return _strip_trailing_zeros(format(out, "f"))
    except (InvalidOperation, Exception):
        return "0"

def _format_size(sz: float, sz_decimals: int) -> str:
    return _format_size_round(sz, sz_decimals, ROUND_DOWN)

def _format_price_side(px: float, sz_decimals: int, is_buy: bool) -> str:
    MAX_DECIMALS = 6
    max_px_decimals = max(0, MAX_DECIMALS - int(sz_decimals or 0))
    rnd = ROUND_UP if is_buy else ROUND_DOWN

    try:
        d = _to_decimal(px)
        if d <= 0:
            return "0"

        if d >= 1:
            int_part = int(d)
            digits_before = len(str(abs(int_part))) if int_part != 0 else 1
            if digits_before >= 5:
                out = d.quantize(Decimal("1"), rounding=rnd)
                return _strip_trailing_zeros(format(out, "f"))

            allowed_sig_decimals = max(0, 5 - digits_before)
            allowed_decimals = min(max_px_decimals, allowed_sig_decimals)
            q = Decimal("1") if allowed_decimals <= 0 else Decimal("1").scaleb(-allowed_decimals)
            out = d.quantize(q, rounding=rnd)
            return _strip_trailing_zeros(format(out, "f"))

        q = Decimal("1") if max_px_decimals <= 0 else Decimal("1").scaleb(-max_px_decimals)
        out = d.quantize(q, rounding=rnd)
        return _strip_trailing_zeros(format(out, "f"))

    except (InvalidOperation, Exception):
        return "0"


def _format_price_tick(px: float, tick_size: float, sz_decimals: int, is_buy: bool) -> str:
    """
    Formatea precio cumpliendo tickSize (múltiplo exacto).
    - BUY: redondea hacia arriba al tick (más agresivo para llenar IOC)
    - SELL: redondea hacia abajo al tick
    Si tick_size no está disponible, cae al formateador por decimales.
    """
    try:
        tick = float(tick_size or 0.0)
        if tick <= 0:
            return _format_price_side(px, sz_decimals, is_buy=is_buy)

        d_px = _to_decimal(px)
        if d_px <= 0:
            return "0"

        d_tick = _to_decimal(tick)

        # nº de ticks (entero)
        rnd = ROUND_UP if is_buy else ROUND_DOWN
        ticks = (d_px / d_tick).to_integral_value(rounding=rnd)
        out = ticks * d_tick

        # normalizar a precisión del tick (evita 0.10000000000002)
        q = d_tick
        out = out.quantize(q, rounding=rnd)

        return _strip_trailing_zeros(format(out, "f"))
    except (InvalidOperation, Exception):
        return _format_price_side(px, sz_decimals, is_buy=is_buy)

# ------------------------------------------------------------
# Balance
# ------------------------------------------------------------


def get_account_snapshot(user_id: int) -> Dict[str, Any]:
    """
    Snapshot vivo de la cuenta en Hyperliquid para lectura de la MiniApp.

    Devuelve un payload robusto que NO lanza excepciones al caller y permite
    saber si la cuenta está lista para operar, si falta capital o si el exchange
    no respondió.
    """
    wallet = get_user_wallet(user_id)
    try:
        private_key = get_user_private_key(user_id)
    except PrivateKeyDecryptError:
        private_key = None
        base: Dict[str, Any] = {
            "wallet_configured": bool(wallet),
            "private_key_configured": True,
            "status": "blocked",
            "label": "Private key inválida",
            "tone": "danger",
            "message": "La private key almacenada no pudo validarse. Reconfigúrala en la MiniApp para reactivar la operativa.",
            "available_balance": 0.0,
            "account_value": 0.0,
            "capital_threshold": float(MIN_CAPITAL),
            "capital_sufficient": False,
            "positions_count": 0,
            "has_open_position": False,
            "active_symbols": [],
            "exchange_reachable": False,
            "credentials_repair_required": True,
        }
        if not wallet:
            base["message"] = "Falta configurar la wallet para consultar el exchange."
            base["label"] = "Falta configuración"
            base["tone"] = "blocked"
        return base

    base: Dict[str, Any] = {
        "wallet_configured": bool(wallet),
        "private_key_configured": bool(private_key),
        "status": "blocked",
        "label": "Falta configuración",
        "tone": "blocked",
        "message": "Configura wallet y private key para poder operar.",
        "available_balance": 0.0,
        "account_value": 0.0,
        "capital_threshold": float(MIN_CAPITAL),
        "capital_sufficient": False,
        "positions_count": 0,
        "has_open_position": False,
        "active_symbols": [],
        "exchange_reachable": False,
    }

    if not wallet:
        base["message"] = "Falta configurar la wallet para consultar el exchange."
        return base

    if not private_key:
        base["message"] = "Falta configurar la private key para habilitar la operativa."
        return base

    r = make_request("/info", {"type": "clearinghouseState", "user": wallet})
    if not isinstance(r, dict):
        base.update({
            "status": "error",
            "label": "Exchange sin respuesta",
            "tone": "danger",
            "message": "No se pudo leer el estado de la cuenta en Hyperliquid.",
            "exchange_reachable": False,
        })
        return base

    def _to_float(x: Any) -> float:
        try:
            return float(x)
        except Exception:
            try:
                return float(str(x).strip())
            except Exception:
                return 0.0

    withdrawable = _to_float(r.get("withdrawable", 0) or 0)
    account_value = _to_float(((r.get("marginSummary") or {}).get("accountValue", 0)) or 0)
    available = withdrawable if withdrawable > 0 else account_value

    asset_positions = r.get("assetPositions") or []
    active_symbols = []
    if isinstance(asset_positions, list):
        for ap in asset_positions:
            if not isinstance(ap, dict):
                continue
            pos = ap.get("position") or ap
            if not isinstance(pos, dict):
                continue
            try:
                szi = float(pos.get("szi", 0) or 0)
            except Exception:
                szi = 0.0
            if abs(szi) <= 0.0:
                continue
            symbol = (pos.get("coin") or ap.get("coin") or "").strip().upper()
            if symbol:
                active_symbols.append(symbol)

    positions_count = len(active_symbols)
    capital_ok = available >= float(MIN_CAPITAL)

    if positions_count > 0:
        status = "manager_only"
        label = "Posición activa"
        tone = "warning"
        message = f"Hay {positions_count} posición(es) abierta(s). El motor debe seguir gestionándolas."
    elif capital_ok:
        status = "ready"
        label = "Listo para operar"
        tone = "active"
        message = f"Cuenta lista. Capital disponible {available:.4f} USDC."
    else:
        status = "insufficient_capital"
        label = "Capital insuficiente"
        tone = "blocked"
        message = f"Capital disponible {available:.4f} USDC. El mínimo configurado es {float(MIN_CAPITAL):.2f}."

    base.update({
        "status": status,
        "label": label,
        "tone": tone,
        "message": message,
        "available_balance": round(float(available), 8),
        "account_value": round(float(account_value), 8),
        "capital_threshold": float(MIN_CAPITAL),
        "capital_sufficient": bool(capital_ok),
        "positions_count": positions_count,
        "has_open_position": positions_count > 0,
        "active_symbols": active_symbols,
        "exchange_reachable": True,
    })
    return base


def get_balance(user_id: int) -> float:
    """
    Capital operativo REAL para abrir nuevas posiciones.
    - Preferimos 'withdrawable' (disponible) en clearinghouseState.
    - Fallback: accountValue (equity) si withdrawable no existe.
    """
    wallet = get_user_wallet(user_id)
    if not wallet:
        return 0.0

    r = make_request("/info", {"type": "clearinghouseState", "user": wallet})
    if not isinstance(r, dict):
        return 0.0

    def _to_float(x) -> float:
        try:
            return float(x)
        except Exception:
            try:
                return float(str(x).strip())
            except Exception:
                return 0.0

    # ✅ Campo recomendado para sizing seguro
    if "withdrawable" in r:
        w = _to_float(r.get("withdrawable", 0))
        if w > 0:
            return w

    # Fallback (equity total) — puede incluir margen en uso/PnL flotante
    try:
        return _to_float((r.get("marginSummary") or {}).get("accountValue", 0) or 0)
    except Exception:
        return 0.0
# ------------------------------------------------------------
# ✅ Detectar si ya hay posición abierta (1 trade a la vez)
# ------------------------------------------------------------



# ------------------------------------------------------------
# Fills / Realized PnL (para que estadísticas == Exchange)
# ------------------------------------------------------------

def get_user_fills(
    user_id: int,
    start_time_ms: Optional[int] = None,
) -> Any:
    """
    Devuelve fills del usuario desde `start_time_ms` (epoch ms) si se pasa.
    Fuente: /info type=userFills

    Nota: El esquema exacto puede variar por versión, así que devolvemos raw
    y filtramos defensivamente en funciones de más alto nivel.
    """
    wallet = get_user_wallet(user_id)
    if not wallet:
        return []

    payload: Dict[str, Any] = {"type": "userFills", "user": wallet}
    if start_time_ms is not None:
        try:
            payload["type"] = "userFillsByTime"
            payload["startTime"] = int(start_time_ms)
        except Exception:
            pass

    r = make_request("/info", payload)
    return r


def get_recent_closed_pnl(
    user_id: int,
    symbol: str,
    since_ms: int,
) -> Dict[str, float]:
    """
    Suma el PnL realizado (closedPnl) y fees de fills recientes para `symbol`
    desde `since_ms` (epoch ms). Devuelve:
      {"pnl": float, "fees": float, "net": float, "fills": int}

    - `closedPnl` viene del exchange (realized PnL)
    - `fee` viene del exchange (fees)
    - `net` = pnl - fees
    """
    coin = norm_coin(symbol)
    out = {"pnl": 0.0, "fees": 0.0, "net": 0.0, "fills": 0}

    raw = get_user_fills(user_id, start_time_ms=since_ms)

    if not isinstance(raw, list):
        # Algunos wrappers pueden devolver {"fills":[...]} u otro contenedor
        if isinstance(raw, dict):
            raw = raw.get("fills") or raw.get("data") or raw.get("result") or []
        else:
            raw = []

    for f in raw:
        if not isinstance(f, dict):
            continue

        # Normalizaciones típicas: coin / symbol / asset
        f_coin = (f.get("coin") or f.get("symbol") or "").strip().upper()
        if coin and norm_coin(f_coin) != coin:
            continue

        # closedPnl puede venir como string/float
        cp = f.get("closedPnl")
        fee = f.get("fee")

        try:
            cp_f = float(cp) if cp is not None else 0.0
        except Exception:
            cp_f = 0.0

        try:
            fee_f = float(fee) if fee is not None else 0.0
        except Exception:
            fee_f = 0.0

        # Solo contamos fills relevantes: cuando hay PnL o fee real
        if cp_f == 0.0 and fee_f == 0.0:
            continue

        out["pnl"] += cp_f
        out["fees"] += fee_f
        out["fills"] += 1

    out["net"] = out["pnl"] - out["fees"]
    # redondeo suave para logs/DB
    out["pnl"] = float(round(out["pnl"], 10))
    out["fees"] = float(round(out["fees"], 10))
    out["net"] = float(round(out["net"], 10))
    return out


def _is_close_fill_hint(fill: Dict[str, Any]) -> bool:
    try:
        dir_raw = str(fill.get("dir") or fill.get("direction") or fill.get("sideLabel") or "").lower()
    except Exception:
        dir_raw = ""
    if any(tok in dir_raw for tok in ("close", "reduce", "tp", "take_profit", "take-profit", "sl", "stop", "trail", "trailing")):
        return True

    try:
        start_pos = float(fill.get("startPosition") or fill.get("start_pos") or 0.0)
    except Exception:
        start_pos = 0.0
    try:
        cp = float(fill.get("closedPnl") or 0.0)
    except Exception:
        cp = 0.0

    return abs(cp) > 0.0 or abs(start_pos) > 0.0


def get_last_closed_trade_snapshot(
    user_id: int,
    symbol: str,
    *,
    opened_after_ms: Optional[int] = None,
    lookback_ms: int = 2 * 60 * 60 * 1000,
    max_group_gap_ms: int = 4000,
    expected_order_id: Optional[str] = None,
    expected_qty: Optional[float] = None,
    direction: str = "",
) -> Dict[str, Any]:
    """
    Lee del exchange el batch MÁS RECIENTE de cierre para `symbol` y devuelve
    los datos exactos del cierre tal como vienen de los fills del exchange.

    Source of truth:
      - closedPnl  -> PnL bruto reportado por exchange
      - fee        -> fee reportado por exchange
      - px/sz      -> precio y tamaño de los fills reales del cierre

    NO agrega una ventana amplia de fills para "inventar" el trade. Intenta aislar
    el último batch de cierre real por order id (oid/orderId) y, si no existe,
    por cercanía temporal.

    Retorna, si encuentra cierre:
      {
        "gross_pnl": float,
        "fees": float,
        "net_pnl": float,
        "fills": int,
        "exit_price": float,
        "closed_qty": float,
        "close_time_ms": int,
        "close_order_id": str,
        "source": "exchange_close_batch",
      }
    """
    if expected_qty is not None:
        from app.trade_accounting import summarize_trade_fills
        if not opened_after_ms:
            return {"fills": 0, "reason": "MISSING_TRADE_START"}
        raw = get_user_fills(user_id, start_time_ms=int(opened_after_ms))
        return summarize_trade_fills(raw, coin=norm_coin(symbol),
            opened_after_ms=int(opened_after_ms), expected_qty=expected_qty,
            direction=direction, expected_order_id=expected_order_id)
    coin = norm_coin(symbol)
    out: Dict[str, Any] = {
        "gross_pnl": 0.0,
        "fees": 0.0,
        "net_pnl": 0.0,
        "fills": 0,
        "exit_price": 0.0,
        "closed_qty": 0.0,
        "close_time_ms": 0,
        "close_order_id": "",
        "source": "",
    }

    now_ms = int(time.time() * 1000)
    since_ms = max(0, int(now_ms - int(lookback_ms)))
    if opened_after_ms is not None:
        try:
            since_ms = max(since_ms, int(opened_after_ms))
        except Exception:
            pass

    raw = get_user_fills(user_id, start_time_ms=since_ms)
    if not isinstance(raw, list):
        if isinstance(raw, dict):
            raw = raw.get("fills") or raw.get("data") or raw.get("result") or []
        else:
            raw = []

    normed = []
    for f in raw:
        if not isinstance(f, dict):
            continue

        f_coin = (f.get("coin") or f.get("symbol") or "").strip().upper()
        if coin and norm_coin(f_coin) != coin:
            continue

        ts = f.get("time")
        if ts is None:
            ts = f.get("timestamp")
        if ts is None:
            ts = f.get("t")
        try:
            ts_i = int(ts)
        except Exception:
            ts_i = 0

        if opened_after_ms is not None and ts_i and ts_i < int(opened_after_ms):
            continue

        oid = f.get("oid") or f.get("orderId") or f.get("order_id") or f.get("order") or None
        try:
            oid_s = str(oid) if oid is not None else ""
        except Exception:
            oid_s = ""

        cp = f.get("closedPnl")
        fee = f.get("fee")
        px = f.get("px") or f.get("price") or f.get("avgPx") or f.get("avg_price")
        sz = f.get("sz") or f.get("size") or f.get("qty") or f.get("quantity")

        try:
            cp_f = float(cp) if cp is not None else 0.0
        except Exception:
            cp_f = 0.0

        try:
            fee_f = float(fee) if fee is not None else 0.0
        except Exception:
            fee_f = 0.0

        try:
            px_f = float(px) if px is not None else 0.0
        except Exception:
            px_f = 0.0

        try:
            sz_f = abs(float(sz)) if sz is not None else 0.0
        except Exception:
            sz_f = 0.0

        # Solo aceptamos fills que claramente pertenezcan a un cierre real del exchange.
        is_close_hint = _is_close_fill_hint(f)

        # Los fills de entrada pueden traer fee>0 pero NO deben contaminar el batch de cierre.
        if not is_close_hint and cp_f == 0.0:
            continue

        normed.append({
            "ts": ts_i,
            "oid": oid_s,
            "cp": cp_f,
            "fee": fee_f,
            "px": px_f,
            "sz": sz_f,
            "is_close_hint": bool(is_close_hint),
        })

    if not normed:
        return out

    normed.sort(key=lambda x: x["ts"], reverse=True)

    expected_oid = str(expected_order_id or "").strip()
    if expected_oid:
        expected_only = [x for x in normed if (x.get("oid") or "").strip() == expected_oid and bool(x.get("is_close_hint"))]
        if expected_only:
            normed = expected_only
        else:
            return out

    anchor = None
    for x in normed:
        if abs(float(x.get("cp") or 0.0)) > 0.0:
            anchor = x
            break
    if anchor is None:
        for x in normed:
            if bool(x.get("is_close_hint")):
                anchor = x
                break
    if anchor is None:
        return out

    anchor_ts = int(anchor.get("ts") or 0)
    anchor_oid = (anchor.get("oid") or "").strip()

    batch = []
    if anchor_oid:
        for x in normed:
            if (x.get("oid") or "").strip() == anchor_oid and bool(x.get("is_close_hint")):
                batch.append(x)
    else:
        for x in normed:
            if abs(int(x.get("ts") or 0) - anchor_ts) <= int(max_group_gap_ms) and bool(x.get("is_close_hint")):
                batch.append(x)

    if not batch:
        return out

    gross = 0.0
    fees = 0.0
    qty = 0.0
    px_qty = 0.0
    close_time_ms = 0
    for x in batch:
        gross += float(x.get("cp") or 0.0)
        fees += float(x.get("fee") or 0.0)
        sz_f = abs(float(x.get("sz") or 0.0))
        px_f = float(x.get("px") or 0.0)
        if sz_f > 0.0:
            qty += sz_f
            px_qty += px_f * sz_f
        close_time_ms = max(close_time_ms, int(x.get("ts") or 0))

    exit_price = (px_qty / qty) if qty > 0.0 else 0.0
    out.update({
        "gross_pnl": float(round(gross, 10)),
        "fees": float(round(fees, 10)),
        "net_pnl": float(round(gross - fees, 10)),
        "fills": int(len(batch)),
        "exit_price": float(round(exit_price, 10)),
        "closed_qty": float(round(qty, 10)),
        "close_time_ms": int(close_time_ms),
        "close_order_id": anchor_oid,
        "source": "exchange_close_batch",
    })
    return out


def get_last_closed_pnl(
    user_id: int,
    symbol: str,
    lookback_ms: int = 2 * 60 * 60 * 1000,
    max_group_gap_ms: int = 4000,
) -> Dict[str, float]:
    """
    Compat: wrapper fino sobre `get_last_closed_trade_snapshot` para quien solo
    necesita gross/fees/net/fills.
    """
    snap = get_last_closed_trade_snapshot(
        user_id,
        symbol,
        opened_after_ms=None,
        lookback_ms=lookback_ms,
        max_group_gap_ms=max_group_gap_ms,
    )
    return {
        "pnl": float(snap.get("gross_pnl", 0.0) or 0.0),
        "fees": float(snap.get("fees", 0.0) or 0.0),
        "net": float(snap.get("net_pnl", 0.0) or 0.0),
        "fills": int(snap.get("fills", 0) or 0),
    }

def get_position_entry_price(user_id: int, coin: str) -> float:
    """
    Devuelve el entryPx REAL de la posición abierta en el exchange para `coin`.
    Usa /info clearinghouseState (misma fuente que get_balance / has_open_position).

    Importante:
      - Retorna 0.0 si no hay posición o no se puede leer.
      - NO crea/cierras nada, solo lectura.
    """
    wallet = get_user_wallet(user_id)
    if not wallet:
        return 0.0

    coin = norm_coin(coin)
    if not coin:
        return 0.0

    r = make_request("/info", {"type": "clearinghouseState", "user": wallet})
    if not isinstance(r, dict):
        return 0.0

    aps = r.get("assetPositions") or []
    if not isinstance(aps, list):
        return 0.0

    for ap in aps:
        if not isinstance(ap, dict):
            continue
        pos = ap.get("position")
        if not isinstance(pos, dict):
            continue

        c = (pos.get("coin") or ap.get("coin") or "").strip().upper()
        if norm_coin(c) != coin:
            continue

        try:
            szi = float(pos.get("szi", 0) or 0)
        except Exception:
            szi = 0.0

        # Solo si hay size real
        if abs(szi) <= 0.0:
            continue

        for key in ("entryPx", "entry_px", "entryPrice", "avgPx", "averagePrice"):
            if key in pos:
                try:
                    v = float(pos.get(key) or 0)
                    if v > 0:
                        return v
                except Exception:
                    pass

    return 0.0


def get_position_size_verified(user_id: int, coin: str) -> Optional[float]:
    """None means unavailable/malformed; zero is a confirmed flat position."""
    import math
    wallet = get_user_wallet(user_id)
    coin = norm_coin(coin)
    if not wallet or not coin:
        return None
    response = make_request("/info", {"type": "clearinghouseState", "user": wallet})
    if not isinstance(response, dict) or response.get('_http_error'):
        return None
    positions = response.get('assetPositions')
    if not isinstance(positions, list):
        return None
    for item in positions:
        if not isinstance(item, dict) or not isinstance(item.get('position'), dict):
            return None
        position = item['position']
        if not position.get('coin') or 'szi' not in position:
            return None
        try:
            size = float(position['szi'])
        except (TypeError, ValueError):
            return None
        if not math.isfinite(size):
            return None
        if norm_coin(position['coin']) == coin:
            return size
    return 0.0


def get_open_position_size(user_id: int, coin: str) -> float:
    """
    Devuelve el size REAL (szi) de la posición abierta para `coin`.
    - Retorna 0.0 si no hay posición o no se puede leer.
    - El signo indica dirección: >0 long, <0 short.
    """
    wallet = get_user_wallet(user_id)
    if not wallet:
        return 0.0

    coin = norm_coin(coin)
    if not coin:
        return 0.0

    r = make_request("/info", {"type": "clearinghouseState", "user": wallet})
    if not isinstance(r, dict):
        return 0.0

    aps = r.get("assetPositions") or []
    if not isinstance(aps, list):
        return 0.0

    for ap in aps:
        if not isinstance(ap, dict):
            continue
        pos = ap.get("position")
        if not isinstance(pos, dict):
            continue

        c = (pos.get("coin") or ap.get("coin") or "").strip().upper()
        if norm_coin(c) != coin:
            continue

        try:
            szi = float(pos.get("szi", 0) or 0)
        except Exception:
            szi = 0.0

        if szi != 0.0:
            return szi

    return 0.0

def has_open_position(user_id: int) -> bool:
    """No side effects. Unknown account state blocks entries by raising."""
    import math
    wallet = get_user_wallet(user_id)
    if not wallet:
        raise RuntimeError('ACCOUNT_WALLET_UNAVAILABLE')
    response = make_request('/info', {'type': 'clearinghouseState', 'user': wallet})
    if not isinstance(response, dict) or response.get('_http_error') or not isinstance(response.get('assetPositions'), list):
        raise RuntimeError('ACCOUNT_POSITION_STATE_UNAVAILABLE')
    for item in response['assetPositions']:
        position = item.get('position') if isinstance(item, dict) else None
        if not isinstance(position, dict) or 'szi' not in position:
            raise RuntimeError('MALFORMED_POSITION_STATE')
        size = float(position['szi'])
        if not math.isfinite(size):
            raise RuntimeError('NONFINITE_POSITION_STATE')
        if size != 0:
            return True
    return False


class HyperliquidSigner:
    def __init__(self, private_key: str):
        try:
            from hyperliquid.utils.signing import sign_l1_action
            from eth_account import Account
            self._account = Account.from_key(private_key)
            self._sign_l1_action = sign_l1_action
        except Exception as e:
            raise RuntimeError("Firma HL: instala hyperliquid-python-sdk") from e

    def sign(
        self,
        action: dict,
        nonce_ms: int,
        vault_address: Optional[str] = None,
        expires_after_ms: Optional[int] = None,
        is_mainnet: Optional[bool] = None
    ) -> Any:
        if expires_after_ms is None:
            expires_after_ms = int(nonce_ms) + 60_000
        if is_mainnet is None:
            is_mainnet = (str(HYPER_BASE_URL).rstrip("/") == "https://api.hyperliquid.xyz")
        return self._sign_l1_action(self._account, action, vault_address, nonce_ms, expires_after_ms, is_mainnet)


def _unwrap_exchange(resp: Any) -> Tuple[str, Any]:
    if not isinstance(resp, dict):
        return ("unknown", resp)
    st = str(resp.get("status") or "").lower()
    if st in ("ok", "err") and "response" in resp:
        return (st, resp.get("response"))
    return ("unknown", resp)

def _extract_statuses(resp: Any) -> list:
    """
    Esperado en OK:
      {"status":"ok","response":{"type":"order","data":{"statuses":[ ... ]}}}
    """
    st, inner = _unwrap_exchange(resp)
    if st != "ok" or not isinstance(inner, dict):
        return []
    data = inner.get("data")
    if not isinstance(data, dict):
        return []
    statuses = data.get("statuses")
    return statuses if isinstance(statuses, list) else []

def _parse_status(status_obj: Any) -> Dict[str, Any]:
    """
    statuses[i] puede venir en varios formatos según la acción:
      - {"filled": {...}}
      - {"error": "..."}
      - {"resting": {...}}
      - "waitingForTrigger" / "open" / "resting"

    En Hyperliquid, ``waitingForTrigger`` significa que la trigger order fue
    aceptada y quedó viva en exchange esperando el disparo. Eso NO es un error.
    """
    out = {"kind": "unknown", "error": "", "filled_sz": 0.0}

    if isinstance(status_obj, str):
        raw = str(status_obj or "").strip()
        normalized = raw.replace("_", "").replace("-", "").replace(" ", "").lower()
        if normalized in ("waitingfortrigger", "resting", "open", "success"):
            out["kind"] = "resting"
            return out
        if normalized in ("filled", "fill"):
            out["kind"] = "filled"
            return out
        if normalized in ("error", "rejected", "reject"):
            out["kind"] = "error"
            out["error"] = raw
            return out
        return out

    if not isinstance(status_obj, dict) or not status_obj:
        return out

    if "error" in status_obj:
        out["kind"] = "error"
        out["error"] = str(status_obj.get("error") or "")
        return out

    if "filled" in status_obj and isinstance(status_obj.get("filled"), dict):
        out["kind"] = "filled"
        f = status_obj.get("filled")
        for k in ("totalSz", "filledSz", "sz"):
            if k in f:
                try:
                    out["filled_sz"] = float(f.get(k) or 0)
                    break
                except Exception:
                    pass
        return out

    if "filled" in status_obj and not isinstance(status_obj.get("filled"), dict):
        out["kind"] = "filled"
        return out

    if "resting" in status_obj:
        out["kind"] = "resting"
        return out

    if "status" in status_obj:
        nested = _parse_status(status_obj.get("status"))
        if nested.get("kind") != "unknown":
            return nested

    out["kind"] = "unknown"
    return out

def _detect_fill(resp: Any) -> Dict[str, Any]:
    out = {"filled": False, "filled_sz": 0.0, "status": "UNKNOWN", "error": ""}

    if not resp:
        out["status"] = "UNKNOWN"
        out["error"] = "EMPTY_RESPONSE"
        return out

    if isinstance(resp, dict) and resp.get("_http_error"):
        out["status"] = "UNKNOWN"
        out["error"] = f"HTTP {resp.get('_http_status')} {resp.get('_http_body')}"
        return out

    st, inner = _unwrap_exchange(resp)
    if st == "err":
        out["status"] = "ERROR"
        out["error"] = str(inner)
        return out

    statuses = _extract_statuses(resp)
    if statuses:
        first = _parse_status(statuses[0])

        if first["kind"] == "error":
            err = (first.get("error") or "").strip()
            out["error"] = err
            if "ioccancel" in err.lower() or "ioc cancel" in err.lower():
                out["status"] = "NO_FILL"
                return out
            out["status"] = "ERROR"
            return out

        if first["kind"] == "filled":
            out["filled_sz"] = float(first.get("filled_sz") or 0.0)
            out["filled"] = out["filled_sz"] > 0
            out["status"] = "FILLED" if out["filled"] else "NO_FILL"
            return out

        if first["kind"] == "resting":
            out["status"] = "UNKNOWN"
            out["error"] = "RESTING_UNEXPECTED"
            return out

    out["status"] = "UNKNOWN"
    out["error"] = "NO_STATUSES_IN_RESPONSE"
    return out

# ------------------------------------------------------------
# Slippage adaptativo
# ------------------------------------------------------------

def _default_slippage(ref_px: float, *, reduce_only: bool = False) -> float:
    _ = ref_px  # compat: el límite ya no depende del precio nominal del activo.
    bps = EXECUTION_EXIT_IOC_BASE_SLIPPAGE_BPS if reduce_only else EXECUTION_IOC_BASE_SLIPPAGE_BPS
    return max(float(bps), 0.0) / 10_000.0


def _clamp_slippage(x: float, *, reduce_only: bool = False) -> float:
    try:
        x = float(x)
    except Exception:
        x = _default_slippage(0.0, reduce_only=reduce_only)
    max_bps = EXECUTION_EXIT_IOC_MAX_SLIPPAGE_BPS if reduce_only else EXECUTION_IOC_MAX_SLIPPAGE_BPS
    max_value = max(float(max_bps), 0.0) / 10_000.0
    return max(0.0, min(x, max_value))


# ------------------------------------------------------------
# Place market order (IOC agresiva)
# ------------------------------------------------------------

_MIN_TRADE_NOTIONAL = 10.0
_MIN_TRADE_BUFFER = 0.20


def get_exchange_min_order_notional_usdc() -> float:
    return float(_MIN_TRADE_NOTIONAL + _MIN_TRADE_BUFFER)

# ------------------------------------------------------------
# Margin mode / leverage (FORZAR ISOLATED)
# ------------------------------------------------------------

FORCE_ISOLATED = True
FORCE_LEVERAGE = LEVERAGE

def _set_isolated_leverage(
    private_key: str,
    asset: int,
    leverage: int,
    vault_address: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Fuerza isolated + leverage para el asset.
    Devuelve {"ok": bool, "reason": str, "raw": Any}
    """
    try:
        lev = int(leverage)
        if lev <= 0:
            lev = 1
    except Exception:
        lev = 1

    nonce = int(time.time() * 1000)
    expires_after_ms = nonce + 60_000

    action = {
        "type": "updateLeverage",
        "asset": asset,
        "isCross": False,
        "leverage": lev,
    }

    try:
        signer = HyperliquidSigner(private_key)
        signature = signer.sign(
            action,
            nonce,
            vault_address=vault_address,
            expires_after_ms=expires_after_ms,
        )
    except Exception as e:
        return {"ok": False, "reason": "SIGN_ERROR", "raw": str(e)}

    payload = {
        "action": action,
        "nonce": nonce,
        "signature": signature,
        "expiresAfter": expires_after_ms,
    }
    if vault_address:
        payload["vaultAddress"] = vault_address

    resp = make_request("/exchange", payload)
    st, inner = _unwrap_exchange(resp)
    if st == "ok":
        return {"ok": True, "reason": "OK", "raw": resp}

    return {"ok": False, "reason": str(inner), "raw": resp}

def place_market_order(
    user_id: int,
    symbol: str,
    side: str,
    qty: float,
    slippage: Optional[float] = None,
    vault_address: Optional[str] = None,
    max_no_fill_retries: int = 1,
    retry_delay_seconds: float = 0.35,
    slippage_step: Optional[float] = None,
    reduce_only: bool = False,
    entry_price_limit: Optional[float] = None,
):
    wallet = get_user_wallet(user_id)
    try:
        private_key = get_user_private_key(user_id)
    except PrivateKeyDecryptError:
        return {"ok": False, "filled": False, "reason": "PRIVATE_KEY_DECRYPT_ERROR"}
    if not wallet or not private_key:
        return {"ok": False, "filled": False, "reason": "NO_WALLET_OR_KEY"}

    coin = norm_coin(symbol)
    asset = get_asset_index(coin)
    if asset is None:
        return {"ok": False, "filled": False, "reason": "NO_ASSET", "coin": coin}

    sz_decimals = get_sz_decimals(asset)
    tick_size = get_tick_size(asset)
    is_buy = side.lower() == "buy"

    # ✅ FORZAR ISOLATED + LEVERAGE (antes de ordenar)
    if FORCE_ISOLATED and not reduce_only:
        lev_resp = _set_isolated_leverage(
            private_key=private_key,
            asset=asset,
            leverage=FORCE_LEVERAGE,
            vault_address=vault_address,
        )
        if not lev_resp.get("ok"):
            must_log(
                f"❌ updateLeverage failed coin={coin} asset={asset} "
                f"lev={FORCE_LEVERAGE} reason={lev_resp.get('reason')}"
            )
            return {
                "ok": False,
                "filled": False,
                "reason": "LEVERAGE_MODE_SET_FAILED",
                "coin": coin,
                "error": lev_resp.get("reason"),
            }

        safe_log(f"✅ Leverage set coin={coin} asset={asset} isolated=True lev={FORCE_LEVERAGE}x")

    initial_book = _get_execution_book_metrics(coin, is_buy=is_buy, impact_bps=EXECUTION_BOOK_IMPACT_BPS)
    bid = float((initial_book or {}).get("best_bid") or 0.0)
    ask = float((initial_book or {}).get("best_ask") or 0.0)
    mid = float(get_price(coin) or 0.0)

    ref_px = (ask if is_buy else bid)
    if ref_px <= 0:
        ref_px = mid
    if ref_px <= 0:
        return {"ok": False, "filled": False, "reason": "NO_PRICE", "coin": coin}

    base_slip = _default_slippage(ref_px, reduce_only=reduce_only) if slippage is None else float(slippage)
    base_slip = _clamp_slippage(base_slip, reduce_only=reduce_only)
    retry_step = (
        max(float(EXECUTION_IOC_RETRY_STEP_BPS), 0.0) / 10_000.0
        if slippage_step is None
        else max(float(slippage_step), 0.0)
    )

    total_attempts = 1 + max(0, int(max_no_fill_retries))

    for attempt in range(1, total_attempts + 1):
        book_metrics = _get_execution_book_metrics(coin, is_buy=is_buy, impact_bps=EXECUTION_BOOK_IMPACT_BPS)
        bid = float((book_metrics or {}).get("best_bid") or 0.0)
        ask = float((book_metrics or {}).get("best_ask") or 0.0)
        mid = float(get_price(coin) or 0.0)
        ref_px = (ask if is_buy else bid)
        if ref_px <= 0:
            ref_px = mid
        if ref_px <= 0:
            return {"ok": False, "filled": False, "reason": "NO_PRICE", "coin": coin}
        if (not reduce_only) and not book_metrics:
            return {"ok": False, "filled": False, "reason": "BOOK_UNAVAILABLE", "coin": coin}

        slip = _clamp_slippage(base_slip + (attempt - 1) * retry_step, reduce_only=reduce_only)
        raw_px = ref_px * (1 + slip) if is_buy else ref_px * (1 - slip)

        p_str = _format_price_tick(raw_px, tick_size, sz_decimals, is_buy=is_buy)
        if entry_price_limit is not None and not reduce_only:
            import math
            limit_px = float(entry_price_limit)
            if not math.isfinite(limit_px) or limit_px <= 0:
                return {'ok': False, 'filled': False, 'reason': 'INVALID_ENTRY_PRICE_LIMIT'}
            # Round the cap inward so a tick cannot consume the permitted RR.
            cap = _format_price_tick(limit_px, tick_size, sz_decimals, is_buy=not is_buy)
            if (is_buy and float(p_str) > float(cap)) or (not is_buy and float(p_str) < float(cap)):
                p_str = cap

        qty = max(0.000001, float(qty))
        s_str = _format_size(qty, sz_decimals)

        try:
            px_f = float(p_str)
            sz_f = float(s_str)
        except Exception:
            return {
                "ok": False,
                "filled": False,
                "reason": "BAD_FORMATTED",
                "coin": coin,
                "px": p_str,
                "sz": s_str,
            }

        notional = px_f * sz_f

        if not reduce_only:
            spread_bps = float((book_metrics or {}).get("spread_bps") or 0.0)
            depth_notional = float((book_metrics or {}).get("depth_notional") or 0.0)
            required_depth = max(ref_px * sz_f, 0.0) * float(EXECUTION_MIN_BOOK_COVERAGE)
            if spread_bps <= 0.0 or spread_bps > float(EXECUTION_MAX_SPREAD_BPS):
                must_log(
                    f"🟠 ENTRY_SPREAD_REJECT coin={coin} side={side} spread_bps={spread_bps:.2f} "
                    f"max={float(EXECUTION_MAX_SPREAD_BPS):.2f}"
                )
                return {
                    "ok": False,
                    "filled": False,
                    "reason": "SPREAD_TOO_WIDE",
                    "coin": coin,
                    "spread_bps": spread_bps,
                    "max_spread_bps": float(EXECUTION_MAX_SPREAD_BPS),
                }
            if depth_notional < required_depth:
                must_log(
                    f"🟠 ENTRY_DEPTH_REJECT coin={coin} side={side} depth~{depth_notional:.2f} "
                    f"required~{required_depth:.2f} impact_bps={float(EXECUTION_BOOK_IMPACT_BPS):.1f}"
                )
                return {
                    "ok": False,
                    "filled": False,
                    "reason": "INSUFFICIENT_BOOK_DEPTH",
                    "coin": coin,
                    "depth_notional": depth_notional,
                    "required_depth_notional": required_depth,
                    "impact_bps": float(EXECUTION_BOOK_IMPACT_BPS),
                }

        if not reduce_only and notional < (_MIN_TRADE_NOTIONAL + _MIN_TRADE_BUFFER):
            required_ntl = float(_MIN_TRADE_NOTIONAL + _MIN_TRADE_BUFFER)
            required_sz = required_ntl / max(px_f, 1e-12)

            # ✅ NO inyectamos size para "forzar" mínimos: respetamos el capital del usuario.
            # Si el exchange requiere un mínimo (notional), devolvemos razón explícita.
            must_log(
                f"🟠 MIN_NOTIONAL coin={coin} side={side} px={p_str} sz={s_str} "
                f"ntl~{round(notional,4)} required~{round(required_ntl,4)}"
            )
            return {
                "ok": False,
                "filled": False,
                "reason": "MIN_NOTIONAL",
                "coin": coin,
                "side": side,
                "px": p_str,
                "sz": s_str,
                "notional": float(notional),
                "min_notional": float(required_ntl),
                "required_sz": float(required_sz),
            }

        nonce = int(time.time() * 1000)
        expires_after_ms = nonce + 60_000

        action = {
            "type": "order",
            "orders": [{
                "a": asset,
                "b": is_buy,
                "p": p_str,
                "s": s_str,
                "r": bool(reduce_only),
                "t": {"limit": {"tif": "Ioc"}},
            }],
            "grouping": "na",
        }

        try:
            signer = HyperliquidSigner(private_key)
            signature = signer.sign(
                action,
                nonce,
                vault_address=vault_address,
                expires_after_ms=expires_after_ms,
            )
        except Exception as e:
            return {"ok": False, "filled": False, "reason": "SIGN_ERROR", "coin": coin, "error": str(e)}

        payload = {"action": action, "nonce": nonce, "signature": signature, "expiresAfter": expires_after_ms}
        if vault_address:
            payload["vaultAddress"] = vault_address

        r = make_request("/exchange", payload)
        det = _detect_fill(r)

        # ---- ERROR real del exchange
        if det["status"] == "ERROR":
            must_log(
                f"❌ EXCHANGE_ERROR coin={coin} side={side} bid={bid} ask={ask} "
                f"px={p_str} sz={s_str} ntl~{round(notional,4)} err={det.get('error')}"
            )
            return {
                "ok": False,
                "filled": False,
                "reason": "EXCHANGE_ERROR",
                "coin": coin,
                "side": side,
                "bid": bid,
                "ask": ask,
                "px": p_str,
                "sz": s_str,
                "notional": float(notional),
                "error": det.get("error", ""),
                "raw": r,
            }

        # ---- FILLED
        if det["status"] == "FILLED":
            safe_log(
                f"🟢 FILLED coin={coin} side={side} px={p_str} sz={s_str} "
                f"ntl~{round(notional,4)} slip={round(slip*100,2)}%"
            )
            return {
                "ok": True,
                "filled": True,
                "reason": "FILLED",
                "coin": coin,
                "side": side,
                "bid": bid,
                "ask": ask,
                "px": p_str,
                "sz": s_str,
                "notional": float(notional),
                "slippage": slip,
                "attempt": attempt,
                "filled_sz": float(det.get("filled_sz") or 0.0),
                "raw": r,
            }

        if det["status"] != "NO_FILL":
            return {"ok": False, "filled": False, "reason": "ORDER_STATUS_UNKNOWN",
                    "coin": coin, "raw": r}

        # ---- NO_FILL (IOC cancel)
        must_log(
            f"🟡 NO_FILL coin={coin} side={side} bid={bid} ask={ask} "
            f"px={p_str} sz={s_str} ntl~{round(notional,4)} err={det.get('error')}"
        )
        if attempt < total_attempts:
            time.sleep(max(0.0, float(retry_delay_seconds)))
            continue

        return {
            "ok": True,
            "filled": False,
            "reason": "NO_FILL",
            "coin": coin,
            "side": side,
            "bid": bid,
            "ask": ask,
            "px": p_str,
            "sz": s_str,
            "notional": float(notional),
            "error": det.get("error", ""),
            "raw": r,
        }

    return {"ok": True, "filled": False, "reason": "NO_FILL", "coin": norm_coin(symbol)}



# ------------------------------------------------------------
# STOP LOSS REAL (trigger order) — ReduceOnly en exchange
# ------------------------------------------------------------

def place_stop_loss(
    user_id: int,
    symbol: str,
    position_side: str,
    qty: float,
    trigger_price: float,
    vault_address: Optional[str] = None,
):
    """
    Coloca un STOP LOSS REAL en el exchange (Trigger Market) con reduceOnly=True.

    - position_side: "long"/"short" (o "buy"/"sell" para la posición)
      * Si la posición es LONG -> el SL es una orden SELL (b=False)
      * Si la posición es SHORT -> el SL es una orden BUY  (b=True)

    Retorna:
      {"ok": True, "reason": "RESTING"|"FILLED", ...}  si el stop fue aceptado
      {"ok": False, "reason": "...", ...}               si el exchange lo rechazó
    """
    wallet = get_user_wallet(user_id)
    try:
        private_key = get_user_private_key(user_id)
    except PrivateKeyDecryptError:
        return {"ok": False, "reason": "PRIVATE_KEY_DECRYPT_ERROR"}
    if not wallet or not private_key:
        return {"ok": False, "reason": "NO_WALLET_OR_KEY"}

    coin = norm_coin(symbol)
    asset = get_asset_index(coin)
    if asset is None:
        return {"ok": False, "reason": "NO_ASSET", "coin": coin}

    sz_decimals = get_sz_decimals(asset)
    tick_size = get_tick_size(asset)

    ps = (position_side or "").strip().lower()
    # position_side describe la posición actual; el SL va en sentido contrario
    # LONG -> SL SELL (b=False). SHORT -> SL BUY (b=True).
    is_buy = False
    if ps in ("short", "sell"):
        is_buy = True

    try:
        trigger_price = float(trigger_price)
    except Exception:
        return {"ok": False, "reason": "BAD_TRIGGER_PRICE", "coin": coin}

    if trigger_price <= 0:
        return {"ok": False, "reason": "BAD_TRIGGER_PRICE", "coin": coin}

    # Formateo con tick size. Para SELL (is_buy=False) redondeamos hacia abajo.
    trig_str = _format_price_tick(trigger_price, tick_size, sz_decimals, is_buy=is_buy)

    qty = max(0.000001, float(qty))
    s_str = _format_size(qty, sz_decimals)

    nonce = int(time.time() * 1000)
    expires_after_ms = nonce + 60_000

    action = {
        "type": "order",
        "orders": [{
            "a": asset,
            "b": bool(is_buy),
            "p": trig_str,
            "s": s_str,
            "r": True,  # reduceOnly SIEMPRE
            "t": {
                "trigger": {
                    "isMarket": True,
                    "triggerPx": trig_str,
                    "tpsl": "sl",
                }
            },
        }],
        "grouping": "na",
    }

    try:
        signer = HyperliquidSigner(private_key)
        signature = signer.sign(
            action,
            nonce,
            vault_address=vault_address,
            expires_after_ms=expires_after_ms,
        )
    except Exception as e:
        return {"ok": False, "reason": "SIGN_ERROR", "coin": coin, "error": str(e)}

    payload = {"action": action, "nonce": nonce, "signature": signature, "expiresAfter": expires_after_ms}
    if vault_address:
        payload["vaultAddress"] = vault_address

    r = make_request("/exchange", payload)

    # Para trigger orders esperamos "resting" (aceptada y esperando) o "filled" (si se dispara instantáneo).
    st, inner = _unwrap_exchange(r)
    if st == "err":
        return {"ok": False, "reason": "EXCHANGE_ERR", "coin": coin, "raw": r}

    statuses = _extract_statuses(r)
    if statuses:
        first = _parse_status(statuses[0])
        if first["kind"] == "error":
            return {"ok": False, "reason": "EXCHANGE_ERROR", "coin": coin, "error": first.get("error", ""), "raw": r}
        if first["kind"] == "filled":
            return {"ok": True, "reason": "FILLED", "coin": coin, "triggerPx": trig_str, "sz": s_str, "raw": r}
        if first["kind"] == "resting":
            return {"ok": True, "reason": "RESTING", "coin": coin, "triggerPx": trig_str, "sz": s_str, "raw": r}

    return {"ok": False, "reason": "NO_STATUSES_IN_RESPONSE", "coin": coin, "raw": r}


def place_take_profit(
    user_id: int,
    symbol: str,
    position_side: str,
    qty: float,
    trigger_price: float,
    vault_address: Optional[str] = None,
):
    """Coloca un TAKE PROFIT REAL en el exchange (Trigger Market) con reduceOnly=True."""
    wallet = get_user_wallet(user_id)
    try:
        private_key = get_user_private_key(user_id)
    except PrivateKeyDecryptError:
        return {"ok": False, "reason": "PRIVATE_KEY_DECRYPT_ERROR"}
    if not wallet or not private_key:
        return {"ok": False, "reason": "NO_WALLET_OR_KEY"}

    coin = norm_coin(symbol)
    asset = get_asset_index(coin)
    if asset is None:
        return {"ok": False, "reason": "NO_ASSET", "coin": coin}

    sz_decimals = get_sz_decimals(asset)
    tick_size = get_tick_size(asset)

    ps = (position_side or "").strip().lower()
    is_buy = ps in ("short", "sell")

    try:
        trigger_price = float(trigger_price)
    except Exception:
        return {"ok": False, "reason": "BAD_TRIGGER_PRICE", "coin": coin}

    if trigger_price <= 0:
        return {"ok": False, "reason": "BAD_TRIGGER_PRICE", "coin": coin}

    trig_str = _format_price_tick(trigger_price, tick_size, sz_decimals, is_buy=is_buy)
    qty = max(0.000001, float(qty))
    s_str = _format_size(qty, sz_decimals)

    nonce = int(time.time() * 1000)
    expires_after_ms = nonce + 60_000

    action = {
        "type": "order",
        "orders": [{
            "a": asset,
            "b": bool(is_buy),
            "p": trig_str,
            "s": s_str,
            "r": True,
            "t": {
                "trigger": {
                    "isMarket": True,
                    "triggerPx": trig_str,
                    "tpsl": "tp",
                }
            },
        }],
        "grouping": "na",
    }

    try:
        signer = HyperliquidSigner(private_key)
        signature = signer.sign(
            action,
            nonce,
            vault_address=vault_address,
            expires_after_ms=expires_after_ms,
        )
    except Exception as e:
        return {"ok": False, "reason": "SIGN_ERROR", "coin": coin, "error": str(e)}

    payload = {"action": action, "nonce": nonce, "signature": signature, "expiresAfter": expires_after_ms}
    if vault_address:
        payload["vaultAddress"] = vault_address

    r = make_request("/exchange", payload)

    st, inner = _unwrap_exchange(r)
    if st == "err":
        return {"ok": False, "reason": "EXCHANGE_ERR", "coin": coin, "raw": r}

    statuses = _extract_statuses(r)
    if statuses:
        first = _parse_status(statuses[0])
        if first["kind"] == "error":
            return {"ok": False, "reason": "EXCHANGE_ERROR", "coin": coin, "error": first.get("error", ""), "raw": r}
        if first["kind"] == "filled":
            return {"ok": True, "reason": "FILLED", "coin": coin, "triggerPx": trig_str, "sz": s_str, "raw": r}
        if first["kind"] == "resting":
            return {"ok": True, "reason": "RESTING", "coin": coin, "triggerPx": trig_str, "sz": s_str, "raw": r}

    return {"ok": False, "reason": "NO_STATUSES_IN_RESPONSE", "coin": coin, "raw": r}


def place_position_tpsl_pair(
    user_id: int,
    symbol: str,
    position_side: str,
    qty: float,
    stop_trigger_price: float,
    take_profit_trigger_price: float,
    vault_address: Optional[str] = None,
):
    """
    Coloca un par TP/SL REAL en el exchange como trigger orders reduceOnly,
    agrupados con ``positionTpsl`` para que el exchange trate ambas protecciones
    como un bracket de posición.
    """
    wallet = get_user_wallet(user_id)
    try:
        private_key = get_user_private_key(user_id)
    except PrivateKeyDecryptError:
        return {"ok": False, "reason": "PRIVATE_KEY_DECRYPT_ERROR"}
    if not wallet or not private_key:
        return {"ok": False, "reason": "NO_WALLET_OR_KEY"}

    coin = norm_coin(symbol)
    asset = get_asset_index(coin)
    if asset is None:
        return {"ok": False, "reason": "NO_ASSET", "coin": coin}

    sz_decimals = get_sz_decimals(asset)
    tick_size = get_tick_size(asset)

    ps = (position_side or "").strip().lower()
    is_buy = ps in ("short", "sell")

    try:
        stop_trigger_price = float(stop_trigger_price)
        take_profit_trigger_price = float(take_profit_trigger_price)
    except Exception:
        return {"ok": False, "reason": "BAD_TRIGGER_PRICE", "coin": coin}

    if stop_trigger_price <= 0 or take_profit_trigger_price <= 0:
        return {"ok": False, "reason": "BAD_TRIGGER_PRICE", "coin": coin}

    qty = max(0.000001, float(qty))
    s_str = _format_size(qty, sz_decimals)
    stop_str = _format_price_tick(stop_trigger_price, tick_size, sz_decimals, is_buy=is_buy)
    tp_str = _format_price_tick(take_profit_trigger_price, tick_size, sz_decimals, is_buy=is_buy)

    nonce = int(time.time() * 1000)
    expires_after_ms = nonce + 60_000

    action = {
        "type": "order",
        "orders": [
            {
                "a": asset,
                "b": bool(is_buy),
                "p": stop_str,
                "s": s_str,
                "r": True,
                "t": {
                    "trigger": {
                        "isMarket": True,
                        "triggerPx": stop_str,
                        "tpsl": "sl",
                    }
                },
            },
            {
                "a": asset,
                "b": bool(is_buy),
                "p": tp_str,
                "s": s_str,
                "r": True,
                "t": {
                    "trigger": {
                        "isMarket": True,
                        "triggerPx": tp_str,
                        "tpsl": "tp",
                    }
                },
            },
        ],
        "grouping": "positionTpsl",
    }

    try:
        signer = HyperliquidSigner(private_key)
        signature = signer.sign(
            action,
            nonce,
            vault_address=vault_address,
            expires_after_ms=expires_after_ms,
        )
    except Exception as e:
        return {"ok": False, "reason": "SIGN_ERROR", "coin": coin, "error": str(e)}

    payload = {"action": action, "nonce": nonce, "signature": signature, "expiresAfter": expires_after_ms}
    if vault_address:
        payload["vaultAddress"] = vault_address

    r = make_request("/exchange", payload)
    if isinstance(r, dict) and r.get("_http_error"):
        return {"ok": False, "reason": "HTTP_ERROR", "coin": coin, "error": f"HTTP {r.get('_http_status')} {r.get('_http_body')}", "raw": r}

    st, inner = _unwrap_exchange(r)
    if st == "err":
        return {"ok": False, "reason": "EXCHANGE_ERR", "coin": coin, "error": str(inner), "raw": r}

    statuses = _extract_statuses(r)
    if not statuses:
        return {"ok": False, "reason": "NO_PAIR_STATUSES", "coin": coin, "error": str(inner), "raw": r}

    parsed = [_parse_status(item) for item in statuses]
    errors = [str(item.get("error") or "") for item in parsed if item.get("kind") == "error"]
    if errors:
        return {
            "ok": False,
            "reason": "EXCHANGE_ERROR",
            "coin": coin,
            "error": " | ".join(err for err in errors if err),
            "statuses": parsed,
            "raw": r,
        }

    accepted_kinds = {"resting", "filled"}
    accepted = [item for item in parsed if item.get("kind") in accepted_kinds]
    if len(accepted) >= 2:
        return {
            "ok": True,
            "reason": "PAIR_ACCEPTED",
            "coin": coin,
            "stopTriggerPx": stop_str,
            "tpTriggerPx": tp_str,
            "sz": s_str,
            "statuses": parsed,
            "raw": r,
        }

    return {
        "ok": False,
        "reason": "PAIR_NOT_ACCEPTED",
        "coin": coin,
        "error": str(inner),
        "statuses": parsed,
        "raw": r,
    }


# ------------------------------------------------------------

# ------------------------------------------------------------
# Cancelación de órdenes (limpieza) — opción A
# - Útil para cancelar el STOP/órdenes pendientes cuando una posición se cierra.
# - Cancela TODAS las órdenes abiertas del asset (símbolo).
# ------------------------------------------------------------



def cancel_reduce_only_triggers_for_symbol_side(
    user_id: int,
    symbol: str,
    position_side: str,
    vault_address: Optional[str] = None,
):
    """Cancela SOLO triggers reduce-only del símbolo y lado indicado.

    Uso principal: limpiar stops/TP huérfanos antes de montar la protección
    de un trade NUEVO sin tocar órdenes no relacionadas.
    """
    coin = norm_coin(symbol)
    expected_side = "A" if str(position_side).lower() == "long" else "B"
    try:
        orders = get_frontend_open_orders(user_id)
    except Exception:
        orders = []

    target_oids: List[int] = []
    for od in orders:
        try:
            if norm_coin(str(od.get("coin") or od.get("symbol") or "")) != coin:
                continue
            if not bool(od.get("isTrigger")):
                continue
            if not bool(od.get("reduceOnly")):
                continue
            if str(od.get("side") or "").upper() != expected_side:
                continue
            oid = _extract_oid_from_open_order(od)
            if oid is None:
                continue
            target_oids.append(int(oid))
        except Exception:
            continue

    deduped = sorted(set(target_oids))
    if not deduped:
        return {
            "ok": True,
            "reason": "NO_REDUCE_ONLY_TRIGGERS",
            "coin": coin,
            "cancelled": [],
        }

    resp = cancel_orders(
        user_id=user_id,
        symbol=symbol,
        order_ids=deduped,
        vault_address=vault_address,
    )
    if isinstance(resp, dict):
        resp.setdefault("coin", coin)
    return resp

def cancel_all_orders_for_symbol(
    user_id: int,
    symbol: str,
    vault_address: Optional[str] = None,
):
    """Cancela las órdenes abiertas del símbolo (asset) para esta wallet/vault.

    Implementación segura para producción:
    - descubre órdenes abiertas vía /info
    - cancela por oid usando la acción documentada ``cancel``
    - no usa ``cancelAll`` porque no está documentado en la API oficial actual
    """
    coin = norm_coin(symbol)
    orders = []
    try:
        orders = get_frontend_open_orders(user_id)
    except Exception:
        orders = []
    if not orders:
        try:
            orders = get_open_orders(user_id)
        except Exception:
            orders = []

    target_oids: List[int] = []
    for od in orders:
        try:
            if norm_coin(str(od.get("coin") or od.get("symbol") or "")) != coin:
                continue
            oid = _extract_oid_from_open_order(od)
            if oid is None:
                continue
            target_oids.append(int(oid))
        except Exception:
            continue

    deduped = sorted(set(target_oids))
    if not deduped:
        return {"ok": True, "reason": "NO_OPEN_ORDERS", "coin": coin, "cancelled": []}

    return cancel_orders(
        user_id=user_id,
        symbol=symbol,
        order_ids=deduped,
        vault_address=vault_address,
    )

# Wrappers
# ------------------------------------------------------------

def open_long(user_id: int, symbol: str, qty: float):
    return place_market_order(user_id, symbol, "buy", qty)

def open_short(user_id: int, symbol: str, qty: float):
    return place_market_order(user_id, symbol, "sell", qty)
