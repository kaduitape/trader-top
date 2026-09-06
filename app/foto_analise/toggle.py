"""Interruptor das analises da IA, acionavel de fora do painel.

## O que ele liga e desliga

Somente a ENTREGA da analise consultiva — o que o indicador desenha no
grafico. Ele nao para o coletor, nao muda o modo do sistema e, acima de
tudo, **nao envia nem cancela ordem nenhuma**: desligar aqui limpa a tela,
nao mexe numa posicao aberta.

Essa fronteira e a razao de o interruptor ser um objeto proprio em vez de
mais um campo em `trading_automation`. Um botao no grafico do MetaTrader
parece um botao de robo, e alguem vai clicar nele achando que esta
parando o robo. Ele nao esta — e a resposta da API diz isso em texto.

## Por que por simbolo

Quem opera dois ativos em duas janelas quer silenciar um sem apagar o
outro. Um interruptor global obrigaria a escolher entre os dois.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.database.repositories.system_setting_repository import SystemSettingRepository

ANALYSIS_TOGGLE_SETTING = "foto_analise.enabled_symbols"

DESCRIPTION = (
    "Analises da IA entregues ao indicador MT5, por simbolo. "
    "Nao afeta ordens, coleta nem modo do sistema."
)


@dataclass(frozen=True, slots=True)
class AnalysisToggle:
    """Ligado por padrao; a lista guarda as EXCECOES.

    Guardar os desligados em vez dos ligados e deliberado: um simbolo novo,
    recem-coletado, comeca funcionando. A alternativa faria cada ativo novo
    nascer mudo, e o operador procuraria bug onde ha configuracao.
    """

    disabled: frozenset[str] = field(default_factory=frozenset)

    def is_enabled(self, symbol: str) -> bool:
        return symbol.strip().upper() not in self.disabled

    def with_symbol(self, symbol: str, *, enabled: bool) -> AnalysisToggle:
        nome = symbol.strip().upper()
        atual = set(self.disabled)
        if enabled:
            atual.discard(nome)
        else:
            atual.add(nome)
        return AnalysisToggle(disabled=frozenset(atual))


def load_analysis_toggle(session: Session) -> AnalysisToggle:
    """Le o estado. Valor corrompido nunca vira "tudo desligado".

    O lado seguro aqui e o LIGADO: uma tela silenciosa por causa de um JSON
    quebrado seria interpretada como "sem oportunidade", que e uma leitura
    de mercado — e nao de configuracao.
    """
    bruto = SystemSettingRepository(session).get(ANALYSIS_TOGGLE_SETTING)
    if not bruto:
        return AnalysisToggle()
    try:
        dados = json.loads(bruto)
    except (TypeError, ValueError):
        return AnalysisToggle()
    if not isinstance(dados, list):
        return AnalysisToggle()
    return AnalysisToggle(
        disabled=frozenset(
            str(item).strip().upper() for item in dados if str(item).strip()
        )
    )


def save_analysis_toggle(session: Session, toggle: AnalysisToggle) -> None:
    SystemSettingRepository(session).set(
        ANALYSIS_TOGGLE_SETTING,
        json.dumps(sorted(toggle.disabled)),
        description=DESCRIPTION,
    )


def set_symbol_enabled(session: Session, symbol: str, *, enabled: bool) -> AnalysisToggle:
    novo = load_analysis_toggle(session).with_symbol(symbol, enabled=enabled)
    save_analysis_toggle(session, novo)
    return novo
