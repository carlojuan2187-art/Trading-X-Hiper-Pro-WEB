# ============================================================
# CONFIGURACIÓN GLOBAL – TRADING X HYPER PRO
# PRODUCCIÓN REAL
# ============================================================

import os
import math

# ============================================================
# BOT DE TELEGRAM
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
BOT_NAME = "TradingXHyperProBot"
BOT_USERNAME = os.getenv("BOT_USERNAME", "TradingXHiperPro_bot").strip().lstrip("@") or "TradingXHiperPro_bot"

# El worker DEMO solo lee mercado público y señales: NO requiere credenciales Telegram.
# En WEB/API/BOT este requisito sigue intacto. Definir ambas flags SOLO en worker.
_IS_DEMO_WORKER = (os.getenv('DEMO_WORKER_MODE', '').strip().lower() == 'true'
                   and os.getenv('DEMO_ENABLED', '').strip().lower() == 'true')
if not TELEGRAM_BOT_TOKEN and not _IS_DEMO_WORKER:
    raise RuntimeError("❌ TELEGRAM_BOT_TOKEN no está definido en variables de entorno")

# ============================================================
# ADMIN (SEGURIDAD)
# ============================================================

ADMIN_TELEGRAM_ID = int(os.getenv("ADMIN_TELEGRAM_ID", "0"))
if ADMIN_TELEGRAM_ID <= 0:
    raise RuntimeError("❌ ADMIN_TELEGRAM_ID no está definido en variables de entorno")

# ============================================================
# CONTACTO ADMIN (WHATSAPP)
# ============================================================

ADMIN_WHATSAPP_LINK = os.getenv("ADMIN_WHATSAPP_LINK", "").strip()

# ============================================================
# BASE DE DATOS – MongoDB Atlas
# ============================================================

MONGO_URI = os.getenv("MONGO_URL")
DB_NAME = os.getenv("MONGO_DB_NAME", "TRADING_X_HIPER_PRO")

if not MONGO_URI:
    raise RuntimeError("❌ MONGO_URL no está definido en variables de entorno")

# ============================================================
# EXCHANGE – HYPERLIQUID (PRODUCCIÓN)
# ============================================================

HYPER_BASE_URL = "https://api.hyperliquid.xyz"
DEFAULT_PAIR = "BTC-USDC"

REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "10"))

# ============================================================
# SISTEMA DE SCANEO AUTOMÁTICO DE MERCADO (PROD)
# ============================================================

SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "15"))
SCANNER_DEPTH = int(os.getenv("SCANNER_DEPTH", "80"))

SCANNER_MIN_24H_NOTIONAL = float(os.getenv("SCANNER_MIN_24H_NOTIONAL", "2000000"))  # 2,000,000
SCANNER_MIN_OPEN_INTEREST = float(os.getenv("SCANNER_MIN_OPEN_INTEREST", "250000"))  # 250,000
SCANNER_MAX_SPREAD_BPS = float(os.getenv("SCANNER_MAX_SPREAD_BPS", "25"))
SCANNER_MIN_TOP_BOOK_NOTIONAL = float(os.getenv("SCANNER_MIN_TOP_BOOK_NOTIONAL", "2000"))  # 2,000 USDC
SCANNER_SHORTLIST_DEPTH_FOR_L2 = int(os.getenv("SCANNER_SHORTLIST_DEPTH_FOR_L2", "25"))

SCANNER_STATS_CACHE_TTL = float(os.getenv("SCANNER_STATS_CACHE_TTL", "5.0"))
SCANNER_L2_CACHE_TTL = float(os.getenv("SCANNER_L2_CACHE_TTL", "1.5"))

# ============================================================
# ESTRATEGIA – (Compatibilidad)
# ============================================================

ENTRY_SIGNAL_THRESHOLD = float(os.getenv("ENTRY_SIGNAL_THRESHOLD", "0.58"))

# ============================================================
# TP / SL (ALINEADO CON trading_engine.py)
# ------------------------------------------------------------
# IMPORTANTE:
# - Todo es % de PRECIO (no ROE, no margen).
# - Las estrategias activas calculan TP/SL fijos por estructura y ATR para cada trade.
# - Estos valores quedan como fallback/compatibilidad para rutas antiguas.
# - La gestión dinámica (break-even/trailing) está desactivada en trading_engine.py.
# ============================================================

# ✅ TP fijo fallback (nombre de variable legado por compatibilidad).
TP_ACTIVATE_TRAIL_PRICE = float(os.getenv("TP_ACTIVATE_TRAIL_PRICE", "0.0117"))   # +1.17% precio

# Legado: se conserva para no romper despliegues existentes, pero el engine no usa trailing.
TRAIL_RETRACE_PRICE     = float(os.getenv("TRAIL_RETRACE_PRICE", "0.00100"))

# ✅ SL fijo fallback para compatibilidad.
SL_MIN_PRICE = float(os.getenv("SL_MIN_PRICE", "0.0117"))  # -1.17% precio
SL_MAX_PRICE = float(os.getenv("SL_MAX_PRICE", "0.0117"))  # fijo = min = max

# ------------------------------------------------------------
# Backwards-compat / legado (por si otra parte del bot usa estos)
# Los igualamos para que NO haya incoherencias.
# ============================================================

TP_MIN = float(os.getenv("TP_MIN", str(TP_ACTIVATE_TRAIL_PRICE)))
TP_MAX = float(os.getenv("TP_MAX", str(TP_ACTIVATE_TRAIL_PRICE)))

SL_MIN = float(os.getenv("SL_MIN", str(SL_MIN_PRICE)))
SL_MAX = float(os.getenv("SL_MAX", str(SL_MAX_PRICE)))

# ============================================================
# GESTIÓN DE RIESGO / EJECUCIÓN
# ============================================================
# MIN_CAPITAL_USDC: saldo mínimo disponible para permitir nuevas entradas.
# POSITION_PERCENT: porcentaje del saldo elegible usado como margen.
# MAX_TRADE_MARGIN_USDC: tope opcional del margen por operación.
#   0 = sin tope; POSITION_PERCENT sigue gobernando el sizing.
# LEVERAGE: apalancamiento entero solicitado a Hyperliquid en isolated.

def _read_positive_float_env(name: str, default: float, *, minimum: float = 0.0) -> float:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = float(raw)
    except Exception as exc:
        raise RuntimeError(f"❌ {name} debe ser numérico; recibido: {raw!r}") from exc
    if not math.isfinite(value) or value < minimum:
        raise RuntimeError(f"❌ {name} debe ser finito y >= {minimum}; recibido: {value}")
    return value


def _read_positive_int_env(name: str, default: int, *, minimum: int = 1) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except Exception as exc:
        raise RuntimeError(f"❌ {name} debe ser un entero; recibido: {raw!r}") from exc
    if value < minimum:
        raise RuntimeError(f"❌ {name} debe ser >= {minimum}; recibido: {value}")
    return value


def _read_bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name, "true" if default else "false").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"❌ {name} debe ser booleano (true/false); recibido: {raw!r}")


MIN_CAPITAL_USDC = _read_positive_float_env("MIN_CAPITAL_USDC", 5.0, minimum=0.0)
# Alias de compatibilidad: otros módulos existentes pueden seguir leyendo MIN_CAPITAL.
MIN_CAPITAL = MIN_CAPITAL_USDC

POSITION_PERCENT = _read_positive_float_env("POSITION_PERCENT", 1.0, minimum=0.0)
if POSITION_PERCENT > 1.0:
    raise RuntimeError(f"❌ POSITION_PERCENT debe estar entre 0 y 1; recibido: {POSITION_PERCENT}")

MAX_TRADE_MARGIN_USDC = _read_positive_float_env("MAX_TRADE_MARGIN_USDC", 0.0, minimum=0.0)
LEVERAGE = _read_positive_int_env("LEVERAGE", 10, minimum=1)

# ✅ Un solo trade a la vez
MAX_CONCURRENT_TRADES = _read_positive_int_env("MAX_CONCURRENT_TRADES", 1, minimum=1)

# ============================================================
# GUARDRAILS DE EJECUCIÓN / CALIDAD
# ============================================================
# Fuente única de verdad para la participación del router y el freno de pérdidas.
STRATEGY_ROUTER_MODE = os.getenv("STRATEGY_ROUTER_MODE", "enforced").strip().lower() or "enforced"
if STRATEGY_ROUTER_MODE not in {"observe_only", "enforced"}:
    raise RuntimeError(f"❌ STRATEGY_ROUTER_MODE inválido: {STRATEGY_ROUTER_MODE!r}")

STRATEGY_ROUTER_USE_CONFIDENT_CANDIDATE = _read_bool_env("STRATEGY_ROUTER_ALLOW_CONFIDENT_CANDIDATE", False)
STRATEGY_ROUTER_ALLOW_RANGE_CANDIDATE = _read_bool_env("STRATEGY_ROUTER_ALLOW_RANGE_CANDIDATE", False)
STRATEGY_ROUTER_ALLOW_TREND_CANDIDATE_BREAKOUT = _read_bool_env("STRATEGY_ROUTER_ALLOW_TREND_CANDIDATE_BREAKOUT", False)
STRATEGY_ROUTER_LIQUIDITY_PROBE_ENABLED = _read_bool_env("STRATEGY_ROUTER_LIQUIDITY_PROBE_ENABLED", True)

MIN_ENTRY_RR = _read_positive_float_env("MIN_ENTRY_RR", 1.15, minimum=0.0)

# Calidad de entrada: evita perseguir señales que ya se desplazaron demasiado.
# El límite efectivo se adapta al ATR, pero nunca supera el hard cap.
ENTRY_MAX_DRIFT_PCT = _read_positive_float_env("ENTRY_MAX_DRIFT_PCT", 0.0025, minimum=0.0)
ENTRY_MAX_DRIFT_ATR_MULT = _read_positive_float_env("ENTRY_MAX_DRIFT_ATR_MULT", 0.45, minimum=0.0)
ENTRY_MAX_DRIFT_HARD_PCT = _read_positive_float_env("ENTRY_MAX_DRIFT_HARD_PCT", 0.0065, minimum=0.0)
# Retrocesos contra la señal pueden dar una mejor entrada y por eso tienen un
# margen algo mayor que el chase en dirección de la operación. Conservamos el
# nombre viejo como fallback para despliegues que ya lo hubieran configurado.
_ENTRY_OLD_ADVERSE_MULT = _read_positive_float_env("ENTRY_MAX_FAVORABLE_DRIFT_MULT", 1.50, minimum=1.0)
ENTRY_MAX_ADVERSE_DRIFT_MULT = _read_positive_float_env("ENTRY_MAX_ADVERSE_DRIFT_MULT", _ENTRY_OLD_ADVERSE_MULT, minimum=1.0)
ENTRY_MAX_FAVORABLE_DRIFT_MULT = ENTRY_MAX_ADVERSE_DRIFT_MULT

# Ejecución: límites expresados en basis points (100 bps = 1%).
# Las aperturas se protegen estrictamente; los reduce-only de salida conservan
# más tolerancia para no impedir un cierre de riesgo.
EXECUTION_MAX_SPREAD_BPS = _read_positive_float_env("EXECUTION_MAX_SPREAD_BPS", 20.0, minimum=0.0)
EXECUTION_BOOK_IMPACT_BPS = _read_positive_float_env("EXECUTION_BOOK_IMPACT_BPS", 25.0, minimum=1.0)
EXECUTION_MIN_BOOK_COVERAGE = _read_positive_float_env("EXECUTION_MIN_BOOK_COVERAGE", 1.10, minimum=0.0)
EXECUTION_IOC_BASE_SLIPPAGE_BPS = _read_positive_float_env("EXECUTION_IOC_BASE_SLIPPAGE_BPS", 12.0, minimum=0.0)
EXECUTION_IOC_RETRY_STEP_BPS = _read_positive_float_env("EXECUTION_IOC_RETRY_STEP_BPS", 8.0, minimum=0.0)
EXECUTION_IOC_MAX_SLIPPAGE_BPS = _read_positive_float_env("EXECUTION_IOC_MAX_SLIPPAGE_BPS", 28.0, minimum=0.0)
EXECUTION_EXIT_IOC_BASE_SLIPPAGE_BPS = _read_positive_float_env("EXECUTION_EXIT_IOC_BASE_SLIPPAGE_BPS", 30.0, minimum=0.0)
EXECUTION_EXIT_IOC_MAX_SLIPPAGE_BPS = _read_positive_float_env("EXECUTION_EXIT_IOC_MAX_SLIPPAGE_BPS", 80.0, minimum=0.0)
USER_RISK_MAX_CONSEC_LOSSES = _read_positive_int_env("USER_RISK_MAX_CONSEC_LOSSES", 2, minimum=1)
USER_RISK_COOLDOWN_SECONDS = _read_positive_int_env("USER_RISK_COOLDOWN_SECONDS", 1800, minimum=60)
USER_RISK_LOSS_COOLDOWN_SECONDS = _read_positive_int_env("USER_RISK_LOSS_COOLDOWN_SECONDS", 1200, minimum=60)
USER_RISK_MIN_PF = _read_positive_float_env("USER_RISK_MIN_PF", 0.95, minimum=0.0)
USER_RISK_PF_WINDOW = _read_positive_int_env("USER_RISK_PF_WINDOW", 6, minimum=3)
USER_RISK_PF_COOLDOWN_SECONDS = _read_positive_int_env("USER_RISK_PF_COOLDOWN_SECONDS", 1800, minimum=60)
GLOBAL_RISK_MAX_CONSEC_LOSSES = _read_positive_int_env("GLOBAL_RISK_MAX_CONSEC_LOSSES", 6, minimum=2)
GLOBAL_RISK_COOLDOWN_SECONDS = _read_positive_int_env("GLOBAL_RISK_COOLDOWN_SECONDS", 3600, minimum=60)
GLOBAL_RISK_MIN_PF = _read_positive_float_env("GLOBAL_RISK_MIN_PF", 0.90, minimum=0.0)
GLOBAL_RISK_PF_WINDOW = _read_positive_int_env("GLOBAL_RISK_PF_WINDOW", 10, minimum=3)
GLOBAL_RISK_PF_COOLDOWN_SECONDS = _read_positive_int_env("GLOBAL_RISK_PF_COOLDOWN_SECONDS", 3600, minimum=60)
_MAX_TRADES_PER_HOUR_RAW = _read_positive_int_env("MAX_TRADES_PER_HOUR", 4, minimum=0)
_MAX_TRADES_PER_DAY_RAW = _read_positive_int_env("MAX_TRADES_PER_DAY", 12, minimum=0)

# Un valor 0 desactiva el límite correspondiente. El engine ya interpreta None
# como "sin límite", por lo que evitamos que 0 bloquee todas las entradas.
MAX_TRADES_PER_HOUR = None if _MAX_TRADES_PER_HOUR_RAW == 0 else _MAX_TRADES_PER_HOUR_RAW
MAX_TRADES_PER_DAY = None if _MAX_TRADES_PER_DAY_RAW == 0 else _MAX_TRADES_PER_DAY_RAW

# ============================================================
# SISTEMA DE FEES
# ============================================================

OWNER_FEE_PERCENT = float(os.getenv("OWNER_FEE_PERCENT", "0.15"))
REFERRAL_FEE_PERCENT = float(os.getenv("REFERRAL_FEE_PERCENT", "0.05"))

DAILY_FEE_COLLECTION_HOUR = int(os.getenv("DAILY_FEE_COLLECTION_HOUR", "23"))
DAILY_FEE_COLLECTION_MINUTE = int(os.getenv("DAILY_FEE_COLLECTION_MINUTE", "59"))

REFERRAL_PAYOUT_DAY = os.getenv("REFERRAL_PAYOUT_DAY", "sunday")
REFERRAL_PAYOUT_HOUR = int(os.getenv("REFERRAL_PAYOUT_HOUR", "23"))
REFERRAL_PAYOUT_MINUTE = int(os.getenv("REFERRAL_PAYOUT_MINUTE", "59"))

# ============================================================
# LOGS / SISTEMA
# ============================================================

VERBOSE_LOGS = (os.getenv("VERBOSE_LOGS", "False").lower() == "true")
PRODUCTION_MODE = (os.getenv("PRODUCTION_MODE", "True").lower() == "true")


# ============================================================
# MINIAPP / API BACKEND
# ============================================================

MINIAPP_URL = os.getenv("MINIAPP_URL", "").strip()
API_PORT = int(os.getenv("PORT", os.getenv("API_PORT", "8000")))

WEBAPP_INITDATA_MAX_AGE_SECONDS = int(
    os.getenv("WEBAPP_INITDATA_MAX_AGE_SECONDS", "300")
)
WEBAPP_SESSION_TTL_SECONDS = int(
    os.getenv("WEBAPP_SESSION_TTL_SECONDS", "43200")
)
WEBAPP_SESSION_SECRET = os.getenv("WEBAPP_SESSION_SECRET", TELEGRAM_BOT_TOKEN)

# Sitio web independiente (se habilita explícitamente tras configurar BotFather).
WEB_LOGIN_ENABLED = os.getenv("WEB_LOGIN_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
WEB_BASE_URL = os.getenv("WEB_BASE_URL", "").strip().rstrip("/")
TELEGRAM_OIDC_CLIENT_ID = os.getenv("TELEGRAM_OIDC_CLIENT_ID", "").strip()
TELEGRAM_OIDC_CLIENT_SECRET = os.getenv("TELEGRAM_OIDC_CLIENT_SECRET", "").strip()
TELEGRAM_OIDC_REDIRECT_URI = os.getenv("TELEGRAM_OIDC_REDIRECT_URI", "").strip()

PRIVATE_KEY_ENCRYPTION_SECRET = os.getenv(
    "PRIVATE_KEY_ENCRYPTION_SECRET",
    WEBAPP_SESSION_SECRET,
)
PRIVATE_KEY_ENCRYPTION_SECRET_FALLBACKS = [
    item.strip()
    for item in os.getenv("PRIVATE_KEY_ENCRYPTION_SECRET_FALLBACKS", "").split(",")
    if item.strip() and item.strip() != PRIVATE_KEY_ENCRYPTION_SECRET
]

MINIAPP_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("MINIAPP_ALLOWED_ORIGINS", "*").split(",")
    if origin.strip()
]


# ============================================================
# PAGOS AUTOMÁTICOS USDT BEP-20
# ============================================================

def get_payment_network() -> str:
    return os.getenv("PAYMENT_NETWORK", "bep20").strip().lower() or "bep20"


def get_payment_token_symbol() -> str:
    return os.getenv("PAYMENT_TOKEN_SYMBOL", "USDT").strip().upper() or "USDT"


def get_payment_token_contract() -> str:
    return os.getenv("PAYMENT_TOKEN_CONTRACT", "").strip().lower()


def get_payment_receiver_address() -> str:
    return os.getenv("PAYMENT_RECEIVER_ADDRESS", "").strip().lower()


def get_bsc_rpc_http_url() -> str:
    return os.getenv("BSC_RPC_HTTP_URL", "").strip()


def get_payment_min_confirmations() -> int:
    try:
        return max(int(os.getenv("PAYMENT_MIN_CONFIRMATIONS", "3")), 1)
    except Exception:
        return 3


def get_payment_order_ttl_minutes() -> int:
    try:
        return max(int(os.getenv("PAYMENT_ORDER_TTL_MINUTES", "30")), 5)
    except Exception:
        return 30


def get_payment_unique_max_delta() -> float:
    try:
        value = float(os.getenv("PAYMENT_UNIQUE_MAX_DELTA", "0.150"))
    except Exception:
        return 0.150
    return max(0.001, min(value, 0.150))


def get_payment_token_decimals() -> int:
    try:
        return max(int(os.getenv("PAYMENT_TOKEN_DECIMALS", "18")), 0)
    except Exception:
        return 18


def get_payment_lookback_blocks() -> int:
    try:
        return max(int(os.getenv("PAYMENT_LOOKBACK_BLOCKS", "2500")), 100)
    except Exception:
        return 2500


def get_payment_configuration_status() -> dict:
    checks = [
        {
            "key": "BSC_RPC_HTTP_URL",
            "label": "RPC BSC",
            "value_present": bool(get_bsc_rpc_http_url()),
        },
        {
            "key": "PAYMENT_TOKEN_CONTRACT",
            "label": "Contrato del token",
            "value_present": bool(get_payment_token_contract()),
        },
        {
            "key": "PAYMENT_RECEIVER_ADDRESS",
            "label": "Wallet receptora",
            "value_present": bool(get_payment_receiver_address()),
        },
    ]
    missing = [item["key"] for item in checks if not item["value_present"]]
    return {
        "ready": not missing,
        "checks": checks,
        "missing_keys": missing,
        "network": get_payment_network(),
        "token_symbol": get_payment_token_symbol(),
    }


def is_payment_configuration_ready() -> bool:
    return bool(get_payment_configuration_status().get("ready"))
