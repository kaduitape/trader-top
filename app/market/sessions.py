"""Sessoes de negociacao do Forex e sua relevancia por moeda.

Responde a pergunta "este par esta no horario BOM dele agora?" — insumo do
seletor de operacional (`app.execution.playbook`), que precisa saber se a
moeda escolhida esta no seu periodo de maior liquidez antes de decidir COMO
operar.

As janelas sao definidas em UTC e representam o horario padrao (inverno do
hemisferio norte). Durante o horario de verao (DST) as bolsas de Londres e
Nova York deslocam ate 1 hora — por isso `SESSION_WINDOWS` e tratado como
aproximacao declarada, nunca como verdade exata do relogio da corretora.
O `AutopilotPlaybook` compensa isso usando a leitura de VOLUME REAL
(`app.market.volume_profile`) como confirmacao: horario e apenas a hipotese,
volume observado e a evidencia. Nenhuma decisao de entrada depende
exclusivamente do relogio.

Modulo puro: sem banco, sem MetaTrader, sem pandas — so datas e regras,
para permanecer inteiramente testavel.
"""

from __future__ import annotations

import enum
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, time


class TradingSession(enum.StrEnum):
    SYDNEY = "SYDNEY"
    TOKYO = "TOKYO"
    LONDON = "LONDON"
    NEW_YORK = "NEW_YORK"


SESSION_LABELS: dict[TradingSession, str] = {
    TradingSession.SYDNEY: "Sydney",
    TradingSession.TOKYO: "Toquio",
    TradingSession.LONDON: "Londres",
    TradingSession.NEW_YORK: "Nova York",
}

SESSION_WINDOWS: dict[TradingSession, tuple[time, time]] = {
    # Janelas em UTC (horario padrao). Uma janela cujo fim e menor que o
    # inicio atravessa a meia-noite (caso de Sydney).
    TradingSession.SYDNEY: (time(21, 0), time(6, 0)),
    TradingSession.TOKYO: (time(0, 0), time(9, 0)),
    TradingSession.LONDON: (time(7, 0), time(16, 0)),
    TradingSession.NEW_YORK: (time(12, 0), time(21, 0)),
}

CURRENCY_PRIME_SESSIONS: dict[str, tuple[TradingSession, ...]] = {
    "USD": (TradingSession.NEW_YORK, TradingSession.LONDON),
    "CAD": (TradingSession.NEW_YORK,),
    "MXN": (TradingSession.NEW_YORK,),
    "EUR": (TradingSession.LONDON,),
    "GBP": (TradingSession.LONDON,),
    "CHF": (TradingSession.LONDON,),
    "ZAR": (TradingSession.LONDON,),
    "TRY": (TradingSession.LONDON,),
    "JPY": (TradingSession.TOKYO,),
    "AUD": (TradingSession.SYDNEY, TradingSession.TOKYO),
    "NZD": (TradingSession.SYDNEY, TradingSession.TOKYO),
    # Metais seguem os centros de precificacao de Londres e Nova York.
    "XAU": (TradingSession.LONDON, TradingSession.NEW_YORK),
    "XAG": (TradingSession.LONDON, TradingSession.NEW_YORK),
}

WEEK_OPEN_WEEKDAY = 6
"""Domingo (`datetime.weekday()` == 6): o mercado abre as 21:00 UTC."""

WEEK_OPEN_HOUR_UTC = 21
WEEK_CLOSE_WEEKDAY = 4
"""Sexta-feira: o mercado fecha as 21:00 UTC."""

WEEK_CLOSE_HOUR_UTC = 21


class SessionRating(enum.StrEnum):
    CLOSED = "CLOSED"
    """Fim de semana — nenhuma sessao roda e nenhuma ordem deve ser enviada."""

    QUIET = "QUIET"
    """Mercado aberto, mas nenhuma sessao relevante para as moedas do par."""

    ACTIVE = "ACTIVE"
    """Uma das duas moedas do par esta na sua sessao principal."""

    PRIME = "PRIME"
    """As duas moedas do par estao em sessao principal ao mesmo tempo."""


RATING_LABELS: dict[SessionRating, str] = {
    SessionRating.CLOSED: "Mercado fechado",
    SessionRating.QUIET: "Horario fraco para este par",
    SessionRating.ACTIVE: "Horario ativo para este par",
    SessionRating.PRIME: "Melhor horario para este par",
}


@dataclass(frozen=True, slots=True)
class SymbolSessionState:
    """Fotografia do relogio para um par especifico em um instante."""

    symbol: str
    base: str
    quote: str
    now_utc: datetime
    market_open: bool
    active_sessions: tuple[TradingSession, ...]
    prime_sessions: tuple[TradingSession, ...]
    """Sessoes ativas AGORA que sao principais para pelo menos uma das moedas."""

    covered_currencies: tuple[str, ...]
    """Moedas do par cuja sessao principal esta ativa agora."""

    is_overlap: bool
    """Duas ou mais sessoes rodando ao mesmo tempo (maior liquidez do dia)."""

    opening_sessions: tuple[TradingSession, ...]
    """Sessoes que abriram ha pouco (`SESSION_OPENING_MINUTES`) — janela
    classica de rompimento."""

    minutes_to_week_close: float | None
    rating: SessionRating
    reasons: tuple[str, ...]

    @property
    def label(self) -> str:
        return RATING_LABELS[self.rating]

    @property
    def active_labels(self) -> tuple[str, ...]:
        return tuple(SESSION_LABELS[session] for session in self.active_sessions)

    @property
    def headline(self) -> str:
        if not self.market_open:
            return "Mercado fechado (fim de semana)."
        sessions = ", ".join(self.active_labels) or "nenhuma sessao"
        return f"{self.label}: {sessions}."


SESSION_OPENING_MINUTES = 60
"""Quantos minutos apos a abertura de uma sessao ainda contam como
"abertura" — janela em que rompimentos costumam ter mais continuidade."""

CLOSE_PROTECTION_MINUTES = 60
"""Faltando menos que isso para o fechamento semanal, nenhuma entrada nova
e considerada: o fim de semana carrega risco de gap que o stop nao cobre."""


def _split_symbol(symbol: str) -> tuple[str, str]:
    """Extrai (base, quote) do codigo do simbolo.

    Aceita sufixos de corretora (``EURUSD.a``, ``XAUUSD_i``) porque o
    conector resolve o nome real da corretora, nao o codigo canonico. Um
    simbolo que nao case com o formato de 6 letras devolve ("", "") — o
    chamador trata isso como cobertura desconhecida, nunca como erro.
    """
    normalized = "".join(ch for ch in symbol.upper() if ch.isalpha())
    if len(normalized) < 6:
        return ("", "")
    return (normalized[:6][:3], normalized[:6][3:6])


def _window_contains(window: tuple[time, time], moment: time) -> bool:
    start, end = window
    if start <= end:
        return start <= moment < end
    return moment >= start or moment < end


def active_sessions(now_utc: datetime) -> tuple[TradingSession, ...]:
    """Sessoes rodando no instante informado (ignora o fim de semana —
    quem checa abertura semanal e `market_is_open`)."""
    moment = now_utc.timetz().replace(tzinfo=None)
    return tuple(
        session
        for session, window in SESSION_WINDOWS.items()
        if _window_contains(window, moment)
    )


def opening_sessions(
    now_utc: datetime, *, within_minutes: int = SESSION_OPENING_MINUTES
) -> tuple[TradingSession, ...]:
    """Sessoes que abriram nos ultimos `within_minutes`."""
    result: list[TradingSession] = []
    for session, (start, _end) in SESSION_WINDOWS.items():
        start_today = now_utc.replace(
            hour=start.hour, minute=start.minute, second=0, microsecond=0
        )
        elapsed = (now_utc - start_today).total_seconds() / 60
        if elapsed < 0:
            # A abertura de hoje ainda nao chegou: considera a de ontem.
            elapsed += 24 * 60
        if 0 <= elapsed <= within_minutes:
            result.append(session)
    return tuple(result)


def market_is_open(now_utc: datetime) -> bool:
    """Forex aberto entre domingo 21:00 UTC e sexta 21:00 UTC.

    Feriados bancarios NAO sao modelados aqui — nesses dias o mercado abre
    com liquidez reduzida, situacao que a leitura de volume real detecta
    como `DEAD`/`LOW` e que o seletor de operacional trata ficando de fora.
    """
    weekday = now_utc.weekday()
    if weekday == 5:  # sabado
        return False
    if weekday == WEEK_CLOSE_WEEKDAY and now_utc.hour >= WEEK_CLOSE_HOUR_UTC:
        return False
    return not (weekday == WEEK_OPEN_WEEKDAY and now_utc.hour < WEEK_OPEN_HOUR_UTC)


def minutes_to_week_close(now_utc: datetime) -> float | None:
    """Minutos ate o fechamento de sexta 21:00 UTC, ou `None` fora da
    sexta-feira (quando a protecao de fim de semana ainda nao se aplica)."""
    if now_utc.weekday() != WEEK_CLOSE_WEEKDAY:
        return None
    close = now_utc.replace(hour=WEEK_CLOSE_HOUR_UTC, minute=0, second=0, microsecond=0)
    if now_utc >= close:
        return 0.0
    return (close - now_utc).total_seconds() / 60


def prime_sessions_for(currency: str) -> tuple[TradingSession, ...]:
    return CURRENCY_PRIME_SESSIONS.get(currency.upper(), ())


def describe_sessions(sessions: Iterable[TradingSession]) -> str:
    labels = [SESSION_LABELS[session] for session in sessions]
    return ", ".join(labels) if labels else "nenhuma"


def evaluate_symbol_session(symbol: str, *, now: datetime | None = None) -> SymbolSessionState:
    """Avalia se `symbol` esta no seu horario nobre agora.

    `now` e sempre convertido para UTC; um datetime ingenuo e assumido como
    UTC (o worker publica tudo em UTC).
    """
    resolved = now or datetime.now(UTC)
    now_utc = resolved.astimezone(UTC) if resolved.tzinfo is not None else resolved.replace(tzinfo=UTC)

    base, quote = _split_symbol(symbol)
    is_open = market_is_open(now_utc)
    running = active_sessions(now_utc) if is_open else ()
    opening = opening_sessions(now_utc) if is_open else ()
    to_close = minutes_to_week_close(now_utc)

    running_set = set(running)
    covered: list[str] = []
    prime: list[TradingSession] = []
    for currency in (base, quote):
        if not currency:
            continue
        matched = tuple(s for s in prime_sessions_for(currency) if s in running_set)
        if matched:
            covered.append(currency)
            prime.extend(s for s in matched if s not in prime)

    reasons: list[str] = []
    if not is_open:
        rating = SessionRating.CLOSED
        reasons.append(
            "Forex fechado: negociacao so retoma domingo as 21:00 UTC."
        )
    elif len(covered) >= 2:
        rating = SessionRating.PRIME
        reasons.append(
            f"{base} e {quote} estao simultaneamente em sessao principal "
            f"({describe_sessions(prime)})."
        )
    elif len(covered) == 1:
        rating = SessionRating.ACTIVE
        reasons.append(
            f"{covered[0]} esta em sessao principal ({describe_sessions(prime)}); "
            f"a outra moeda do par, nao."
        )
    else:
        rating = SessionRating.QUIET
        reasons.append(
            f"Nenhuma sessao principal de {base or '?'}/{quote or '?'} esta aberta "
            f"agora (rodando: {describe_sessions(running)})."
        )

    if is_open and len(running) >= 2:
        reasons.append(
            f"Sobreposicao de sessoes ({describe_sessions(running)}) — maior "
            "liquidez e spread tipicamente menor."
        )
    if opening:
        reasons.append(
            f"Abertura recente de {describe_sessions(opening)} (ultimos "
            f"{SESSION_OPENING_MINUTES} minutos)."
        )
    if to_close is not None and to_close <= CLOSE_PROTECTION_MINUTES:
        reasons.append(
            f"Faltam {to_close:.0f} minuto(s) para o fechamento semanal — "
            "janela de protecao contra gap de fim de semana."
        )
    if not base or not quote:
        reasons.append(
            f"Simbolo '{symbol}' fora do formato de par de 6 letras — cobertura "
            "por moeda nao pode ser confirmada, tratada como desconhecida."
        )

    return SymbolSessionState(
        symbol=symbol,
        base=base,
        quote=quote,
        now_utc=now_utc,
        market_open=is_open,
        active_sessions=running,
        prime_sessions=tuple(prime),
        covered_currencies=tuple(covered),
        is_overlap=is_open and len(running) >= 2,
        opening_sessions=opening,
        minutes_to_week_close=to_close,
        rating=rating,
        reasons=tuple(reasons),
    )


def is_weekend_protection_window(state: SymbolSessionState) -> bool:
    """Entrada nova deve ser recusada por proximidade do fechamento semanal."""
    return (
        state.minutes_to_week_close is not None
        and state.minutes_to_week_close <= CLOSE_PROTECTION_MINUTES
    )
