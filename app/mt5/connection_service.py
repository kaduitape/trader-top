"""MT5ConnectionService — falar com o terminal, e so isso.

A camada HTTP nao sabe o que e `mt5.initialize`; este servico nao sabe o
que e requisicao. Quem chama passa credencial e recebe um resultado
descrito; nenhuma decisao de apresentacao acontece aqui.

## O que a biblioteca realmente e

`MetaTrader5` NAO e uma API HTTP da corretora. E um cliente local que fala
com o TERMINAL instalado na maquina:

    este servico -> MetaTrader5 (Python) -> Terminal MT5 -> corretora

Consequencias praticas, e as duas ja custaram tempo neste projeto:

1. **So funciona no Windows, na mesma maquina do terminal.** O painel roda
   em container Linux e nunca vai conseguir chamar isto — por isso o teste
   pelo painel e DELEGADO ao worker Windows, e nao executado na requisicao.
2. **Inicializar e diferente de autenticar.** O terminal pode subir e a
   conta ser recusada. Tratar os dois como um passo so produz "erro ao
   conectar" para causas opostas: terminal fechado e senha errada.

## Ordem sem ordens

Este modulo NAO envia ordem. Nem por engano: nada aqui importa
`app.mt5.orders`. Testar conexao autentica e le a conta — mais nada.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 30

# Codigos do terminal traduzidos para o que o operador faz a respeito.
# "RES_E_AUTH_FAILED" nao diz a ninguem se e a senha ou o servidor.
_ERROR_HINTS: dict[int, str] = {
    -1: "Falha generica do terminal.",
    -2: "Argumento invalido enviado ao terminal.",
    -3: "Versao do terminal incompativel com a biblioteca.",
    -4: "Terminal MT5 nao encontrado ou nao pode ser iniciado.",
    -5: "Falha de comunicacao com o terminal MT5.",
    -6: (
        "Autorizacao recusada: confira login, senha e servidor. "
        "O servidor precisa ser exatamente o nome que aparece no MetaTrader."
    ),
    -7: "Metodo nao suportado por esta versao do terminal.",
    -8: "Tempo esgotado falando com o terminal.",
    -10: "Terminal MT5 nao inicializado.",
    10004: "Requisicao recusada pela corretora.",
    10014: "Volume invalido.",
    10018: "Mercado fechado.",
    10019: "Sem dinheiro suficiente na conta.",
}


@dataclass(frozen=True, slots=True)
class AccountInfo:
    """O que a tela mostra depois de um teste bem-sucedido."""

    login: int
    name: str | None
    server: str
    company: str | None
    currency: str
    balance: float
    equity: float
    margin: float
    margin_free: float
    leverage: int
    is_demo: bool

    @property
    def account_type(self) -> str:
        return "DEMO" if self.is_demo else "REAL"


@dataclass(frozen=True, slots=True)
class ConnectionResult:
    success: bool
    message: str
    account: AccountInfo | None = None
    error_code: int | None = None
    tested_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class Diagnostics:
    """Fatos verificaveis, nenhum sensivel. Sem senha, nem mascarada."""

    library_installed: bool
    library_version: str | None
    terminal_path: str | None
    terminal_found: bool
    terminal_initialized: bool
    server_configured: bool
    account_configured: bool
    last_error_code: int | None
    last_error_message: str | None


def describe_error(code: int | None, description: str | None) -> str:
    """Mensagem util a partir do erro do terminal.

    "Erro ao conectar" nao permite acao nenhuma. O codigo sozinho tambem
    nao. A dica e o que transforma o erro em proximo passo.
    """
    dica = _ERROR_HINTS.get(code) if code is not None else None
    partes = [item for item in (dica, description) if item]
    if code is not None:
        partes.append(f"(codigo {code})")
    return " ".join(partes) if partes else "Falha nao identificada pelo terminal."


class MT5ConnectionService:
    """Ciclo de vida de UMA sessao de teste com o terminal.

    Sempre em bloco: quem inicializa desliga. Deixar o terminal inicializado
    por um teste que falhou faz o proximo teste herdar estado sujo, e o
    sintoma aparece longe da causa.
    """

    def __init__(self, *, client=None, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> None:
        self._client = client
        self._timeout = max(5, timeout_seconds)
        self._initialized = False
        self._attempted = False
        """`initialize` pode devolver False DEPOIS de ja ter subido o
        processo do terminal. Desligar so quando deu certo deixaria esse
        processo orfao, e o proximo teste herdaria o estado sujo."""

    # --- biblioteca -------------------------------------------------------

    def _load(self):
        if self._client is not None:
            return self._client
        try:
            import MetaTrader5  # type: ignore[import-not-found]
        except ImportError:
            return None
        return MetaTrader5

    def library_available(self) -> bool:
        return self._load() is not None

    def library_version(self) -> str | None:
        client = self._load()
        if client is None:
            return None
        versao = getattr(client, "__version__", None)
        return str(versao) if versao else None

    # --- ciclo de vida ----------------------------------------------------

    def initialize(self, *, terminal_path: str | None = None) -> ConnectionResult:
        """Sobe o terminal. NAO autentica — sao passos distintos."""
        client = self._load()
        if client is None:
            return ConnectionResult(
                success=False,
                message=(
                    "Biblioteca MetaTrader5 nao instalada neste processo. "
                    "Ela so existe para Windows e precisa rodar na mesma "
                    "maquina do terminal."
                ),
            )

        if terminal_path and not Path(terminal_path).exists():
            return ConnectionResult(
                success=False,
                message=f"Terminal MT5 nao encontrado em {terminal_path}.",
            )

        kwargs: dict = {"timeout": self._timeout * 1000}
        if terminal_path:
            kwargs["path"] = terminal_path

        self._attempted = True
        try:
            ok = client.initialize(**kwargs)
        except Exception as exc:  # pragma: no cover - falha do terminal
            logger.exception("mt5_initialize_failed")
            return ConnectionResult(
                success=False, message=f"Falha ao iniciar o terminal: {exc}"[:300]
            )

        if not ok:
            codigo, descricao = self._last_error(client)
            return ConnectionResult(
                success=False,
                message=describe_error(codigo, descricao),
                error_code=codigo,
            )

        self._initialized = True
        return ConnectionResult(success=True, message="Terminal inicializado.")

    def connect(self, *, login: int, password: str, server: str) -> ConnectionResult:
        """Autentica na conta. Exige `initialize` antes."""
        client = self._load()
        if client is None or not self._initialized:
            return ConnectionResult(
                success=False, message="Terminal MT5 nao inicializado."
            )

        try:
            ok = client.login(
                login, password=password, server=server, timeout=self._timeout * 1000
            )
        except Exception as exc:  # pragma: no cover
            # A excecao pode conter o pedido inteiro; nunca registrar cru.
            logger.exception("mt5_login_failed", extra={"mt5_login": login, "mt5_server": server})
            return ConnectionResult(
                success=False,
                message=f"Falha ao autenticar: {type(exc).__name__}",
            )

        if not ok:
            codigo, descricao = self._last_error(client)
            logger.warning(
                "mt5_login_rejected",
                # Nem a senha nem o cifrado entram aqui — de proposito.
                extra={"mt5_login": login, "mt5_server": server, "mt5_error_code": codigo},
            )
            return ConnectionResult(
                success=False,
                message=describe_error(codigo, descricao),
                error_code=codigo,
            )

        return ConnectionResult(success=True, message="Autenticado.")

    def get_account_info(self) -> AccountInfo | None:
        client = self._load()
        if client is None or not self._initialized:
            return None
        try:
            info = client.account_info()
        except Exception:  # pragma: no cover
            logger.exception("mt5_account_info_failed")
            return None
        if info is None:
            return None

        return AccountInfo(
            login=int(getattr(info, "login", 0)),
            name=getattr(info, "name", None) or None,
            server=str(getattr(info, "server", "") or ""),
            company=getattr(info, "company", None) or None,
            currency=str(getattr(info, "currency", "") or ""),
            balance=float(getattr(info, "balance", 0.0) or 0.0),
            equity=float(getattr(info, "equity", 0.0) or 0.0),
            margin=float(getattr(info, "margin", 0.0) or 0.0),
            margin_free=float(getattr(info, "margin_free", 0.0) or 0.0),
            leverage=int(getattr(info, "leverage", 0) or 0),
            is_demo=self._is_demo(info),
        )

    def disconnect(self) -> None:
        """Desliga o terminal. Basta TER TENTADO inicializar.

        `initialize` pode falhar depois de ja ter subido o processo do
        terminal; condicionar o shutdown ao sucesso deixaria esse processo
        orfao, e o teste seguinte falharia por estado sujo — longe da causa.
        """
        client = self._load()
        if client is not None and (self._initialized or self._attempted):
            try:
                client.shutdown()
            except Exception:  # pragma: no cover
                logger.exception("mt5_shutdown_failed")
        self._initialized = False
        self._attempted = False

    # --- operacao completa ------------------------------------------------

    def test_connection(
        self, *, login: int, password: str, server: str, terminal_path: str | None = None
    ) -> ConnectionResult:
        """Inicializa, autentica, le a conta e DESLIGA — sempre.

        O `finally` nao e detalhe: sem ele, um teste que falha no meio deixa
        o terminal preso, e o proximo teste falha por um motivo que nao tem
        nada a ver com a credencial.
        """
        inicio = time.monotonic()
        try:
            partida = self.initialize(terminal_path=terminal_path)
            if not partida.success:
                return partida

            autenticacao = self.connect(login=login, password=password, server=server)
            if not autenticacao.success:
                return autenticacao

            conta = self.get_account_info()
            if conta is None:
                # Autenticou e a conta nao respondeu: sessao invalida. Sem
                # esta checagem, o painel diria "conectado" sobre uma sessao
                # que nao serve para nada.
                return ConnectionResult(
                    success=False,
                    message=(
                        "Autenticacao aceita, mas a conta nao respondeu — "
                        "sessao invalida. Verifique a conexao do terminal "
                        "com a corretora."
                    ),
                )

            decorrido = time.monotonic() - inicio
            return ConnectionResult(
                success=True,
                message=f"Conectado em {decorrido:.1f}s.",
                account=conta,
            )
        finally:
            self.disconnect()

    def diagnose(
        self, *, login: int | None, server: str | None, terminal_path: str | None
    ) -> Diagnostics:
        """Fatos sobre o ambiente, sem autenticar e sem revelar segredo."""
        client = self._load()
        encontrado = bool(terminal_path) and Path(terminal_path or "").exists()

        inicializado = False
        codigo: int | None = None
        descricao: str | None = None
        if client is not None:
            resultado = self.initialize(terminal_path=terminal_path)
            inicializado = resultado.success
            codigo = resultado.error_code
            descricao = None if resultado.success else resultado.message
            self.disconnect()

        return Diagnostics(
            library_installed=client is not None,
            library_version=self.library_version(),
            terminal_path=terminal_path or None,
            terminal_found=encontrado,
            terminal_initialized=inicializado,
            server_configured=bool(server),
            account_configured=bool(login),
            last_error_code=codigo,
            last_error_message=descricao,
        )

    # --- auxiliares -------------------------------------------------------

    @staticmethod
    def _last_error(client) -> tuple[int | None, str | None]:
        try:
            codigo, descricao = client.last_error()
        except Exception:  # pragma: no cover
            return None, None
        return (int(codigo) if codigo is not None else None), (
            str(descricao) if descricao else None
        )

    @staticmethod
    def _is_demo(info) -> bool:
        """Conta demo? O MT5 expoe isso como `trade_mode` (0 = demo)."""
        modo = getattr(info, "trade_mode", None)
        if modo is not None:
            return int(modo) == 0
        servidor = str(getattr(info, "server", "") or "").lower()
        return "demo" in servidor
