"""Ponte para um MetaTrader 5 rodando sob Wine, em outro container.

## Ponte externa

O projeto inteiro assumia uma coisa: `MetaTrader5` so existe para Windows,
logo o terminal so pode ser alcancado de uma maquina Windows. Por isso o
teste de conexao e a coleta foram desenhados para rodar num worker
separado, la, e o painel Linux so pedia e esperava.

Com o terminal sob Wine num container, essa premissa cai. O terminal passa
a estar alcancavel a partir do Linux — nao por HTTP, e sim por uma ponte
que expõe o modulo `MetaTrader5` de dentro do Wine:

    painel (Linux) -> RPyC -> Python do Wine -> MetaTrader5 -> Terminal -> corretora

O detalhe que faz isso valer a pena: o que volta pela ponte e um PROXY DO
MODULO. Ele responde `initialize`, `login`, `account_info`, `last_error`
exatamente como o pacote nativo. Nada em `MT5ConnectionService` precisa
saber se esta falando com o pacote local ou com a ponte — a diferenca
termina aqui.

## O que continua diferente

Latencia. Cada chamada vira ida e volta em socket. Para testar conexao e
ler conta e irrelevante; para varrer ticks em laco apertado, nao seria — e
por isso a coleta continua onde esta.

E a ponte NAO e um canal seguro: RPyC classico permite executar codigo do
outro lado. Ela so deve escutar em rede interna do Docker, nunca exposta na
internet. O README diz isso; aqui fica registrado porque quem le este
arquivo e quem poderia publicar a porta sem pensar.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

DEFAULT_BRIDGE_PORT = 18812
DEFAULT_CONNECT_TIMEOUT = 15.0


class BridgeError(RuntimeError):
    """Nao foi possivel falar com a ponte. Mensagem sempre acionavel."""


@dataclass(slots=True)
class BridgeSession:
    """Modulo remoto + a conexao que o sustenta.

    As duas coisas andam juntas: fechar a conexao invalida o proxy, entao
    quem guarda um nao pode perder o outro.
    """

    module: object
    _connection: object

    def close(self) -> None:
        try:
            self._connection.close()
        except Exception:  # pragma: no cover - fechar nunca deve estourar
            logger.exception("mt5_bridge_close_failed")


def connect_bridge(
    host: str,
    port: int = DEFAULT_BRIDGE_PORT,
    *,
    timeout: float = DEFAULT_CONNECT_TIMEOUT,
) -> BridgeSession:
    """Abre a ponte e devolve o modulo `MetaTrader5` do outro lado."""
    try:
        import rpyc  # noqa: F401
        from rpyc.utils.classic import connect
    except ImportError as exc:
        raise BridgeError(
            "Biblioteca `rpyc` nao instalada — ela e o que fala com o "
            "container do MetaTrader sob Wine."
        ) from exc

    try:
        conexao = connect(host, port, keepalive=True)
    except OSError as exc:
        # Recusa de conexao e o erro mais comum e o mais mal explicado por
        # padrao: quase sempre e o container parado ou a porta errada.
        raise BridgeError(
            f"Nao foi possivel conectar em {host}:{port} — confira se o "
            f"container do MetaTrader esta rodando e se a porta da ponte "
            f"esta publicada. ({type(exc).__name__})"
        ) from exc
    except Exception as exc:
        raise BridgeError(f"Falha ao abrir a ponte {host}:{port}: {exc}"[:300]) from exc

    try:
        conexao._config["sync_request_timeout"] = timeout
    except Exception:  # pragma: no cover - versao de rpyc sem esse ajuste
        logger.debug("mt5_bridge_timeout_not_applied")

    try:
        modulo = conexao.modules.MetaTrader5
    except Exception as exc:
        conexao.close()
        raise BridgeError(
            "A ponte respondeu, mas o modulo `MetaTrader5` nao existe do "
            "outro lado. A imagem precisa ter o pacote instalado no Python "
            "que roda dentro do Wine."
        ) from exc

    return BridgeSession(module=modulo, _connection=conexao)


def describe_target(host: str | None, port: int) -> str:
    """Como a tela chama o destino, sem revelar nada sensivel."""
    return f"{host}:{port}" if host else "pacote local (Windows)"
