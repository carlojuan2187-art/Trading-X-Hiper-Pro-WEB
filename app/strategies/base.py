from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict


class BaseStrategy(ABC):
    """Contrato base mínimo para estrategias del engine."""

    strategy_id: str = "base"
    strategy_version: str = "v0"
    strategy_model: str = "base"

    @abstractmethod
    def evaluate(self, symbol: str, market_context: Dict[str, Any] | None = None) -> Dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def get_trade_management_params(self, strength: float, score: float, atr_pct: float | None = None) -> Dict[str, Any]:
        raise NotImplementedError
