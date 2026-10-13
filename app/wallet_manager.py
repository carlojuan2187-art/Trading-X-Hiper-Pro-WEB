# ============================================================
# WALLET MANAGER – Trading X Hyper Pro
# Custodia y pagos on-chain del ADMIN (PRODUCCIÓN REAL)
# BLINDADO PARA BANK GRADE
# ============================================================

import os
import time
import logging
from decimal import Decimal

from web3 import Web3
from eth_account import Account

# ============================================================
# LOGGING
# ============================================================

logger = logging.getLogger("WalletManager")
logger.setLevel(logging.INFO)

# ============================================================
# VARIABLES DE ENTORNO (OBLIGATORIAS)
# ============================================================

ADMIN_WALLET_ADDRESS = os.getenv("ADMIN_WALLET_ADDRESS")
ADMIN_PRIVATE_KEY = os.getenv("ADMIN_PRIVATE_KEY")
RPC_URL = os.getenv("RPC_URL")
CHAIN_ID = os.getenv("CHAIN_ID")

USDC_CONTRACT_ADDRESS = os.getenv("USDC_CONTRACT_ADDRESS")  # opcional (ERC20)
USDC_DECIMALS = int(os.getenv("USDC_DECIMALS", "6"))

# ============================================================
# VALIDACIÓN ESTRICTA (FAIL FAST)
# ============================================================

missing = []
if not ADMIN_WALLET_ADDRESS:
    missing.append("ADMIN_WALLET_ADDRESS")
if not ADMIN_PRIVATE_KEY:
    missing.append("ADMIN_PRIVATE_KEY")
if not RPC_URL:
    missing.append("RPC_URL")
if not CHAIN_ID:
    missing.append("CHAIN_ID")

if missing:
    raise RuntimeError(f"❌ Variables de entorno faltantes: {', '.join(missing)}")

CHAIN_ID = int(CHAIN_ID)

# ============================================================
# WEB3 INIT
# ============================================================

w3 = Web3(Web3.HTTPProvider(RPC_URL))
if not w3.is_connected():
    raise RuntimeError("❌ No se pudo conectar al RPC")

admin_wallet = Web3.to_checksum_address(ADMIN_WALLET_ADDRESS)
admin_account = Account.from_key(ADMIN_PRIVATE_KEY)

if admin_account.address.lower() != admin_wallet.lower():
    raise RuntimeError("❌ La PRIVATE KEY no corresponde a la wallet ADMIN")

# ============================================================
# ABI MINIMAL ERC20 (USDC)
# ============================================================

ERC20_ABI = [
    {
        "constant": False,
        "inputs": [
            {"name": "_to", "type": "address"},
            {"name": "_value", "type": "uint256"}
        ],
        "name": "transfer",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function",
    }
]

usdc_contract = None
if USDC_CONTRACT_ADDRESS:
    usdc_contract = w3.eth.contract(
        address=Web3.to_checksum_address(USDC_CONTRACT_ADDRESS),
        abi=ERC20_ABI
    )

# ============================================================
# HELPERS
# ============================================================

def _get_nonce():
    try:
        return w3.eth.get_transaction_count(admin_wallet)
    except Exception as e:
        logger.error(f"Error obteniendo nonce: {e}")
        raise RuntimeError("❌ No se pudo obtener nonce")

def _wait_for_receipt(tx_hash, timeout=120):
    start = time.time()
    while True:
        try:
            receipt = w3.eth.get_transaction_receipt(tx_hash)
            if receipt:
                return receipt
        except Exception:
            pass

        if time.time() - start > timeout:
            raise TimeoutError("⏳ Timeout esperando confirmación on-chain")

        time.sleep(3)

# ============================================================
# PAGO NATIVO (GAS / MONEDA DE RED)
# ============================================================

def send_native_payment(to_address: str, amount: float, concept: str):
    to = Web3.to_checksum_address(to_address)
    value_wei = w3.to_wei(Decimal(str(amount)), "ether")

    tx = {
        "chainId": CHAIN_ID,
        "nonce": _get_nonce(),
        "to": to,
        "value": value_wei,
        "gas": 21000,
        "gasPrice": w3.eth.gas_price,
    }

    signed = w3.eth.account.sign_transaction(tx, ADMIN_PRIVATE_KEY)
    tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)

    logger.info(f"🚀 Pago NATIVO enviado [{concept}] → {tx_hash.hex()}")

    receipt = _wait_for_receipt(tx_hash)
    if receipt.status != 1:
        raise RuntimeError("❌ Transacción fallida")

    return tx_hash.hex()

# ============================================================
# PAGO USDC (ERC20)
# ============================================================

def send_usdc_payment(to_address: str, amount: float, concept: str):
    if not usdc_contract:
        raise RuntimeError("❌ USDC_CONTRACT_ADDRESS no configurado")

    to = Web3.to_checksum_address(to_address)
    value = int(Decimal(str(amount)) * (10 ** USDC_DECIMALS))

    tx = usdc_contract.functions.transfer(to, value).build_transaction({
        "chainId": CHAIN_ID,
        "from": admin_wallet,
        "nonce": _get_nonce(),
        "gas": 100000,
        "gasPrice": w3.eth.gas_price,
    })

    signed = w3.eth.account.sign_transaction(tx, ADMIN_PRIVATE_KEY)
    tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)

    logger.info(f"🚀 Pago USDC enviado [{concept}] → {tx_hash.hex()}")

    receipt = _wait_for_receipt(tx_hash)
    if receipt.status != 1:
        raise RuntimeError("❌ Transferencia USDC fallida")

    return tx_hash.hex()

# ============================================================
# API PÚBLICA (ÚNICA INTERFAZ)
# ============================================================

def pay_admin_fee(amount: float, currency: str = "USDC"):
    if amount <= 0:
        return None

    if currency.upper() == "USDC":
        return send_usdc_payment(admin_wallet, amount, "ADMIN_FEE")

    return send_native_payment(admin_wallet, amount, "ADMIN_FEE")


def pay_referral_fee(referrer_wallet: str, amount: float, currency: str = "USDC"):
    if amount <= 0:
        return None

    if currency.upper() == "USDC":
        return send_usdc_payment(referrer_wallet, amount, "REFERRAL_FEE")

    return send_native_payment(referrer_wallet, amount, "REFERRAL_FEE")
