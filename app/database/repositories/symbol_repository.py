"""Repositorio de simbolos negociaveis."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models.symbol import Symbol
from app.mt5.symbol_mapper import SymbolSpecification


def _decimal(value: float) -> Decimal:
    """Converte via `str()` para evitar herdar a imprecisao binaria do
    `float` (ex.: `Decimal(0.1)` != `Decimal("0.1")`) ao gravar em colunas
    `Numeric`."""
    return Decimal(str(value))


class SymbolRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_name(self, name: str) -> Symbol | None:
        stmt = select(Symbol).where(Symbol.name == name)
        return self._session.execute(stmt).scalar_one_or_none()

    def list_active(self) -> list[Symbol]:
        """Lista os ativos selecionaveis no dashboard em ordem alfabetica.

        A origem continua sendo o catalogo sincronizado do MetaTrader. Isso
        evita oferecer pares que talvez nao existam na corretora conectada.
        """
        stmt = select(Symbol).where(Symbol.is_active.is_(True)).order_by(Symbol.name)
        return list(self._session.execute(stmt).scalars().all())

    def upsert_from_specification(self, spec: SymbolSpecification) -> Symbol:
        """Cria ou atualiza o simbolo com a especificacao mais recente lida
        do MetaTrader. Especificacoes podem mudar (ex.: volume_min de uma
        corretora), entao sempre sincronizamos os campos em vez de ignorar
        um simbolo ja existente."""
        symbol = self.get_by_name(spec.name)
        if symbol is None:
            symbol = Symbol(
                name=spec.name,
                description=spec.description,
                digits=spec.digits,
                point=_decimal(spec.point),
                volume_min=_decimal(spec.volume_min),
                volume_max=_decimal(spec.volume_max),
                volume_step=_decimal(spec.volume_step),
                trade_contract_size=_decimal(spec.trade_contract_size),
                is_active=spec.visible,
            )
            self._session.add(symbol)
        else:
            symbol.description = spec.description
            symbol.digits = spec.digits
            symbol.point = _decimal(spec.point)
            symbol.volume_min = _decimal(spec.volume_min)
            symbol.volume_max = _decimal(spec.volume_max)
            symbol.volume_step = _decimal(spec.volume_step)
            symbol.trade_contract_size = _decimal(spec.trade_contract_size)
            symbol.is_active = spec.visible

        self._session.flush()
        return symbol
