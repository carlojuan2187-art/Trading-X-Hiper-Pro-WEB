# ============================================================
# LOG CONFIG – Trading X Hyper Pro
# Sistema de logs profesional con rotación automática
# ============================================================

import logging
from logging.handlers import RotatingFileHandler
import os

LOG_DIR = "logs"

# Crear carpeta si no existe
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)

# Archivos separados para máxima auditoría
FILES = {
    "bot": "logs/bot.log",
    "trading": "logs/trading.log",
    "system": "logs/system.log"
}

def setup_logger(name: str, filename: str):
    """Crea un logger con rotación automática y evita handlers duplicados."""
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    # 🔒 Evita duplicación de logs si el módulo se importa varias veces
    if not logger.handlers:
        handler = RotatingFileHandler(
            filename,
            maxBytes=2 * 1024 * 1024,   # 2 MB por archivo
            backupCount=5               # 5 copias antiguas
        )

        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )

        handler.setFormatter(formatter)
        logger.addHandler(handler)

        # Evita propagación al root logger
        logger.propagate = False

    return logger


# Loggers globales
bot_logger = setup_logger("BOT", FILES["bot"])
trading_logger = setup_logger("TRADING", FILES["trading"])
system_logger = setup_logger("SYSTEM", FILES["system"])
