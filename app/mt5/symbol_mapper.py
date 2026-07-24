"""Especificacoes de simbolo e normalizacao de preco/volume.

Estas funcoes de normalizacao nao enviam ordens — servem para preparar
valores corretos (arredondados ao numero de casas decimais e ao step de
volume do simbolo) que serao usados por fases futuras (risco, execucao).
Incluidas ja na Fase 2 porque dependem apenas da especificacao do simbolo,
que e informacao somente-leitura.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from app.mt5.client import MT5ClientProtocol

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SymbolSpecification:
    name: str
    description: str
    digits: int
    point: float
    volume_min: float
    volume_max: float
    volume_step: float
    trade_contract_size: float
    spread: int
    trade_mode: int
    visible: bool


def list_symbols(client: MT5ClientProtocol, group: str | None = None) -> list[str]:
    # A extensao nativa do MT5 nao trata ``group=None`` como a omissao do
    # argumento: em alguns terminais ela retorna ``False``. Chamar sem
    # kwargs e necessario para listar o catalogo inteiro.
    symbols = client.symbols_get() if group is None else client.symbols_get(group=group)
    if symbols is None or symbols is False:
        code, description = client.last_error()
        logger.warning(
            "mt5_symbols_get_failed",
            extra={"mt5_error_code": code, "mt5_error_description": description},
        )
        return []
    return [str(getattr(s, "name", "")) for s in symbols]


def fetch_symbol_specification(
    client: MT5ClientProtocol, symbol: str
) -> SymbolSpecification | None:
    info = client.symbol_info(symbol)
    if info is None:
        code, description = client.last_error()
        logger.warning(
            "mt5_symbol_info_failed",
            extra={"symbol": symbol, "mt5_error_code": code, "mt5_error_description": description},
        )
        return None

    return SymbolSpecification(
        name=str(getattr(info, "name", symbol)),
        description=str(getattr(info, "description", "")),
        digits=int(getattr(info, "digits", 5)),
        point=float(getattr(info, "point", 0.00001)),
        volume_min=float(getattr(info, "volume_min", 0.01)),
        volume_max=float(getattr(info, "volume_max", 100.0)),
        volume_step=float(getattr(info, "volume_step", 0.01)),
        trade_contract_size=float(getattr(info, "trade_contract_size", 100000.0)),
        spread=int(getattr(info, "spread", 0)),
        trade_mode=int(getattr(info, "trade_mode", 0)),
        visible=bool(getattr(info, "visible", False)),
    )


def normalize_price(price: float, spec: SymbolSpecification) -> float:
    """Arredonda o preco para o numero de casas decimais do simbolo."""
    quantum = Decimal(1).scaleb(-spec.digits)
    return float(Decimal(str(price)).quantize(quantum, rounding=ROUND_HALF_UP))


def normalize_volume(volume: float, spec: SymbolSpecification) -> float:
    """Arredonda o volume para o step do simbolo e o restringe a
    [volume_min, volume_max]. Nunca retorna um volume fora dos limites do
    simbolo, mesmo que o valor de entrada esteja."""
    if spec.volume_step <= 0:
        rounded = volume
    else:
        steps = round(volume / spec.volume_step)
        rounded = steps * spec.volume_step

    clamped = min(max(rounded, spec.volume_min), spec.volume_max)
    # Corrige erros de ponto flutuante (ex.: 0.30000000000000004).
    decimals = max(0, -int(math.floor(math.log10(spec.volume_step))) if spec.volume_step > 0 else 2)
    return round(clamped, decimals)
