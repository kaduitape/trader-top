"""Transporte TCP+TLS para a cTrader Open API (JSON, porta 5036).

**Esta e a unica peca do adaptador cTrader que NAO pode ser verificada sem
uma conta real.** Tudo o que erra em silencio — conversao de volume,
resolucao de simbolo, sequencia de autenticacao, montagem do pedido — vive em
`protocol.py`/`client.py` e tem teste deterministico. Aqui embaixo so ha
socket, TLS e enquadramento de mensagem, que ou funcionam contra o servidor
de verdade ou falham de forma barulhenta.

Enquadramento: cada mensagem e precedida por seu tamanho em 4 bytes
big-endian. Sem isso, duas mensagens coladas no mesmo pacote TCP viram um
JSON invalido — e a leitura ingenua "um recv, um json.loads" funciona nos
testes manuais e quebra em producao, justamente quando o fluxo aumenta.

A API pode intercalar eventos nao solicitados (execucao, cotacao) no mesmo
canal. Por isso `request` casa a resposta pelo `clientMsgId` e guarda o que
chegou fora de ordem, em vez de devolver o primeiro envelope que aparecer.
"""

from __future__ import annotations

import json
import logging
import socket
import ssl
import struct
import time
from typing import Any

from app.broker.ctrader.protocol import DEMO_HOST, JSON_PORT, LIVE_HOST
from app.broker.port import BrokerError

logger = logging.getLogger(__name__)

_LENGTH_PREFIX = 4


class CTraderTcpTransport:
    """Conexao TLS persistente com um proxy da Open API."""

    def __init__(
        self,
        *,
        demo: bool = True,
        host: str | None = None,
        port: int = JSON_PORT,
        timeout: float = 10.0,
    ) -> None:
        self._host = host or (DEMO_HOST if demo else LIVE_HOST)
        self._port = port
        self._timeout = timeout
        self._sock: ssl.SSLSocket | None = None
        self._pending: list[dict[str, Any]] = []

    # ---- conexao --------------------------------------------------------

    def connect(self) -> None:
        if self._sock is not None:
            return
        contexto = ssl.create_default_context()
        cru = socket.create_connection((self._host, self._port), timeout=self._timeout)
        self._sock = contexto.wrap_socket(cru, server_hostname=self._host)
        logger.info(
            "ctrader_transport_connected", extra={"host": self._host, "port": self._port}
        )

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None
                self._pending.clear()

    # ---- enquadramento --------------------------------------------------

    def _send_raw(self, message: dict[str, Any]) -> None:
        assert self._sock is not None
        corpo = json.dumps(message).encode("utf-8")
        self._sock.sendall(struct.pack(">I", len(corpo)) + corpo)

    def _read_exactly(self, quantidade: int) -> bytes:
        assert self._sock is not None
        buffer = b""
        while len(buffer) < quantidade:
            pedaco = self._sock.recv(quantidade - len(buffer))
            if not pedaco:
                raise BrokerError("A cTrader encerrou a conexao durante a leitura.")
            buffer += pedaco
        return buffer

    def _read_message(self) -> dict[str, Any]:
        tamanho = struct.unpack(">I", self._read_exactly(_LENGTH_PREFIX))[0]
        return json.loads(self._read_exactly(tamanho).decode("utf-8"))

    # ---- interface do cliente -------------------------------------------

    def request(self, message: dict[str, Any], *, timeout: float = 10.0) -> dict[str, Any]:
        self.connect()
        esperado = message.get("clientMsgId")
        self._send_raw(message)

        limite = time.monotonic() + timeout
        while time.monotonic() < limite:
            recebido = self._read_message()
            if recebido.get("clientMsgId") == esperado:
                return recebido
            # Evento nao solicitado: guarda e continua esperando a resposta
            # certa. Devolve-lo aqui seria responder outra pergunta.
            self._pending.append(recebido)
            if len(self._pending) > 100:
                self._pending.pop(0)

        raise BrokerError(
            f"A cTrader nao respondeu a mensagem {esperado} em {timeout:.0f}s."
        )

    def drain_events(self) -> list[dict[str, Any]]:
        """Eventos que chegaram fora de ordem desde a ultima leitura."""
        eventos = list(self._pending)
        self._pending.clear()
        return eventos
