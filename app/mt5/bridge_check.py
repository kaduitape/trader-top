"""Diagnostico passo a passo da ponte com o MetaTrader sob Wine.

"Nao conecta" nao e um diagnostico — e a ausencia de um. Entre o painel e o
terminal existem seis coisas que podem estar erradas, cada uma com uma
correcao diferente:

1. `rpyc` nao instalado no container do painel (imagem antiga)
2. nome do servico nao resolve (containers em redes Docker diferentes)
3. TCP nao conecta (container parado, ou porta so interna e o painel esta
   fora daquela rede)
4. handshake RPyC falha (porta certa, servico errado atras dela)
5. `MetaTrader5` ausente do lado do Wine
6. terminal nao inicializa (MT5 fechado dentro do container)

Este modulo testa os seis em ordem e para no primeiro que falhar, dizendo
qual foi. Cada passo isola UMA hipotese: sem isso, a mesma mensagem
serviria para causas que nao tem nada em comum.
"""

from __future__ import annotations

import contextlib
import socket
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Step:
    name: str
    ok: bool
    detail: str

    @property
    def icon(self) -> str:
        return "OK  " if self.ok else "FALHA"


@dataclass(frozen=True, slots=True)
class BridgeReport:
    host: str
    port: int
    steps: list[Step] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.steps) and all(step.ok for step in self.steps)

    @property
    def first_failure(self) -> Step | None:
        return next((step for step in self.steps if not step.ok), None)


def _check_library() -> Step:
    try:
        import rpyc
    except ImportError:
        return Step(
            "Biblioteca rpyc",
            False,
            "nao instalada NESTE processo. Se o painel roda em container, a "
            "imagem foi construida antes de `rpyc` entrar nas dependencias: "
            "rode `docker compose up -d --build`.",
        )
    return Step("Biblioteca rpyc", True, f"versao {rpyc.__version__}")


# Nomes que as imagens mais comuns de MetaTrader sob Wine usam, mais o
# atalho para o host. Nao e adivinhacao solta: sao os nomes que aparecem nos
# composes publicados dessas imagens, e o custo de tentar e um TCP curto.
_CANDIDATOS = (
    "mt5-wine",
    "mt5",
    "metatrader",
    "metatrader5",
    "mt5linux",
    "trader-top-mt5-wine-1",
    "host.docker.internal",
)


def suggest_hosts(port: int, *, timeout: float = 1.5, skip: str = "") -> list[str]:
    """Nomes alcancaveis nesta porta, entre os candidatos conhecidos.

    Existe porque "o nome nao resolve" nao diz qual nome usar, e quem esta
    olhando o painel nao tem `docker ps` a mao. Um TCP curto contra uma
    lista pequena responde a pergunta que o operador realmente tem.
    """
    encontrados: list[str] = []
    for nome in _CANDIDATOS:
        if nome == skip:
            continue
        try:
            with socket.create_connection((nome, port), timeout=timeout):
                encontrados.append(nome)
        except OSError:
            continue
    return encontrados


def _check_dns(host: str, port: int = 0) -> Step:
    try:
        endereco = socket.gethostbyname(host)
    except OSError:
        detalhe = (
            f"`{host}` nao resolve. Em Docker, o nome do servico so resolve "
            "para containers na MESMA rede — confira se painel e MetaTrader "
            "compartilham a rede."
        )
        if port:
            achados = suggest_hosts(port, skip=host)
            if achados:
                detalhe += (
                    f" Respondem na porta {port} a partir daqui: "
                    f"{', '.join(achados)} — use um desses no campo "
                    "'Host da ponte'."
                )
            else:
                detalhe += (
                    " Nenhum nome conhecido responde nesta porta: o container "
                    "do MetaTrader provavelmente nao esta nesta rede (ou nao "
                    "esta rodando)."
                )
        return Step("Nome resolve", False, detalhe)
    return Step("Nome resolve", True, f"{host} -> {endereco}")


def _check_tcp(host: str, port: int, *, timeout: float) -> Step:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
    except TimeoutError:
        return Step(
            "Porta acessivel",
            False,
            f"tempo esgotado em {host}:{port}. Firewall ou container "
            "inacessivel a partir daqui.",
        )
    except OSError as exc:
        # O errno vai junto de proposito: "recusou" cobre causas opostas.
        # ECONNREFUSED e "ninguem escutando ali"; EHOSTUNREACH e roteamento;
        # e um erro de familia de endereco costuma ser filtro de saida do
        # ambiente que roda o teste, nao problema do destino.
        motivo = exc.strerror or type(exc).__name__
        return Step(
            "Porta acessivel",
            False,
            f"{host}:{port} nao aceitou conexao: {motivo}. Container parado, "
            "porta so interna e o painel fora da rede, ou porta diferente.",
        )
    return Step("Porta acessivel", True, f"{host}:{port} aceita conexao")


def _check_rpyc(host: str, port: int, *, timeout: float) -> tuple[Step, object | None]:
    from app.mt5.bridge import BridgeError, connect_bridge

    try:
        sessao = connect_bridge(host, port, timeout=timeout)
    except BridgeError as exc:
        return Step("Ponte RPyC", False, str(exc)), None
    return Step("Ponte RPyC", True, "modulo MetaTrader5 disponivel"), sessao


def _check_terminal(modulo) -> Step:
    try:
        ok = modulo.initialize()
    except Exception as exc:  # pragma: no cover - falha do lado do Wine
        return Step("Terminal responde", False, f"{type(exc).__name__}: {exc}"[:200])

    if not ok:
        try:
            codigo, descricao = modulo.last_error()
        except Exception:  # pragma: no cover
            codigo, descricao = None, None
        from app.mt5.connection_service import describe_error

        return Step(
            "Terminal responde",
            False,
            describe_error(codigo, descricao)
            + " Abra o noVNC e confirme que o MetaTrader esta aberto.",
        )

    try:
        versao = modulo.version()
    except Exception:  # pragma: no cover
        versao = None
    finally:
        with contextlib.suppress(Exception):
            modulo.shutdown()
    return Step("Terminal responde", True, f"terminal inicializado (versao {versao})")


def check_bridge(host: str, port: int, *, timeout: float = 10.0) -> BridgeReport:
    """Roda os passos em ordem e para no primeiro que falhar.

    Parar no primeiro e deliberado: se o nome nao resolve, o resultado do
    teste de porta seria ruido — e ruido em diagnostico faz procurar no
    lugar errado.
    """
    passos: list[Step] = []

    passos.append(_check_library())
    if not passos[-1].ok:
        return BridgeReport(host=host, port=port, steps=passos)

    passos.append(_check_dns(host, port))
    if not passos[-1].ok:
        return BridgeReport(host=host, port=port, steps=passos)

    passos.append(_check_tcp(host, port, timeout=timeout))
    if not passos[-1].ok:
        return BridgeReport(host=host, port=port, steps=passos)

    passo, sessao = _check_rpyc(host, port, timeout=timeout)
    passos.append(passo)
    if sessao is None:
        return BridgeReport(host=host, port=port, steps=passos)

    try:
        passos.append(_check_terminal(sessao.module))
    finally:
        sessao.close()

    return BridgeReport(host=host, port=port, steps=passos)
