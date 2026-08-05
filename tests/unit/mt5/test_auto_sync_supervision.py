"""O conector tem que ficar de pe sozinho.

Antes, qualquer excecao dentro do laco subia ate o topo e matava o
processo: um segundo de banco indisponivel derrubava o conector, o Windows
tentava algumas vezes e desistia, e a unica saida visivel virava reinstalar
tudo.

O que estes testes protegem: falha em um ciclo custa UM ciclo. Nada alem de
`stop()` encerra o laco.
"""

from __future__ import annotations

from threading import Event

import pytest

from app.mt5.auto_sync import MT5AutoSyncWorker


class _Contador:
    """Conta ciclos e falha nos primeiros, para provar que o laco continua."""

    def __init__(self, worker: MT5AutoSyncWorker, *, falhar: int, parar_em: int) -> None:
        self.chamadas = 0
        self._worker = worker
        self._falhar = falhar
        self._parar_em = parar_em

    def __call__(self) -> None:
        self.chamadas += 1
        if self.chamadas >= self._parar_em:
            self._worker.stop()
        if self.chamadas <= self._falhar:
            raise RuntimeError("banco indisponivel")


@pytest.fixture
def worker(monkeypatch) -> MT5AutoSyncWorker:
    w = MT5AutoSyncWorker(stop_event=Event())
    # Publicar e ler estado exigem banco; aqui o assunto e o laco.
    monkeypatch.setattr(w, "_publish", lambda status: status)
    monkeypatch.setattr(w, "_read_control", lambda: (None, None))
    monkeypatch.setattr(w, "_disconnect", lambda: None)
    monkeypatch.setattr(w, "_report_failure", lambda exc, falhas: None)
    monkeypatch.setattr(w, "_shutdown", lambda: None)
    # Nao esperar de verdade: o backoff e comportamento, o relogio nao.
    monkeypatch.setattr(w._stop, "wait", lambda _timeout=None: None)
    return w


def test_a_failing_cycle_does_not_kill_the_worker(worker, monkeypatch) -> None:
    """A regra que faltava: um ciclo ruim e um ciclo perdido, nao o fim."""
    contador = _Contador(worker, falhar=3, parar_em=5)
    monkeypatch.setattr(worker, "_tick", contador)

    worker.run()

    assert contador.chamadas == 5


def test_it_keeps_going_after_many_consecutive_failures(worker, monkeypatch) -> None:
    """Falha continua costuma ser algo externo (terminal fechado, rede). O
    conector espera mais, mas nao desiste."""
    contador = _Contador(worker, falhar=50, parar_em=20)
    monkeypatch.setattr(worker, "_tick", contador)

    worker.run()

    assert contador.chamadas == 20


def test_stop_ends_the_loop(worker, monkeypatch) -> None:
    contador = _Contador(worker, falhar=0, parar_em=1)
    monkeypatch.setattr(worker, "_tick", contador)

    worker.run()

    assert contador.chamadas == 1


def test_a_failure_disconnects_so_the_next_cycle_reconnects_clean(
    worker, monkeypatch
) -> None:
    """Uma conexao em estado ruim precisa ser derrubada: reaproveita-la faria
    o ciclo seguinte falhar pelo mesmo motivo, para sempre."""
    desconexoes = []
    monkeypatch.setattr(worker, "_disconnect", lambda: desconexoes.append(1))
    contador = _Contador(worker, falhar=2, parar_em=3)
    monkeypatch.setattr(worker, "_tick", contador)

    worker.run()

    assert len(desconexoes) == 2


def test_the_backoff_grows_with_consecutive_failures(worker, monkeypatch) -> None:
    """Martelar a cada segundo um problema externo so enche o log."""
    esperas: list[float] = []
    monkeypatch.setattr(worker._stop, "wait", lambda timeout=None: esperas.append(timeout))
    contador = _Contador(worker, falhar=3, parar_em=4)
    monkeypatch.setattr(worker, "_tick", contador)

    worker.run()

    assert esperas == [5, 10, 15]


def test_the_backoff_is_capped(worker, monkeypatch) -> None:
    """Sem teto, uma noite de falhas levaria a espera a horas — e o conector
    nao voltaria sozinho quando o problema fosse resolvido."""
    esperas: list[float] = []
    monkeypatch.setattr(worker._stop, "wait", lambda timeout=None: esperas.append(timeout))
    contador = _Contador(worker, falhar=30, parar_em=30)
    monkeypatch.setattr(worker, "_tick", contador)

    worker.run()

    assert max(esperas) == 60


def test_a_successful_cycle_resets_the_backoff(worker, monkeypatch) -> None:
    """Depois que o problema passa, a proxima falha recomeca do inicio — e
    nao de onde a sequencia anterior parou."""
    esperas: list[float] = []
    monkeypatch.setattr(worker._stop, "wait", lambda timeout=None: esperas.append(timeout))

    sequencia = [True, True, False, True]  # falha, falha, sucesso, falha
    estado = {"i": 0}

    def tick() -> None:
        i = estado["i"]
        estado["i"] += 1
        if i >= len(sequencia):
            worker.stop()
            return
        if sequencia[i]:
            raise RuntimeError("falha")

    monkeypatch.setattr(worker, "_tick", tick)

    worker.run()

    assert esperas == [5, 10, 5]
