"""Credenciais de uma conta MetaTrader 5 e o resultado do ultimo teste.

Ja e uma TABELA, e nao uma linha em `system_settings`, por dois motivos: o
campo cifrado merece coluna propria (fica obvio no schema o que e sensivel)
e a evolucao pedida — varias contas, alternar demo/real — precisa de chave
primaria de verdade.

Por isso `is_active`: hoje existe uma credencial ativa por vez, mas a forma
da tabela ja comporta varias sem migracao nova.

O que NAO fica aqui: nada que o sistema nao precise. Sem nome do titular,
sem e-mail, sem telefone. Esses dados chegam do proprio MT5 quando o teste
roda e sao exibidos na hora — guardar seria acumular material sensivel sem
uso.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin

ACCOUNT_TYPE_DEMO = "DEMO"
ACCOUNT_TYPE_REAL = "REAL"
ACCOUNT_TYPES = (ACCOUNT_TYPE_DEMO, ACCOUNT_TYPE_REAL)

TEST_STATUS_SUCCESS = "success"
TEST_STATUS_FAILURE = "failure"
TEST_STATUS_PENDING = "pending"


class Mt5Credential(Base, TimestampMixin):
    __tablename__ = "mt5_credentials"

    id: Mapped[int] = mapped_column(primary_key=True)

    login: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    password_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    """Fernet (ver `app/core/crypto.py`). Nunca sai daqui em claro para o
    navegador, para a API ou para o log."""

    server: Mapped[str] = mapped_column(String(120), nullable=False)
    terminal_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    account_type: Mapped[str] = mapped_column(String(8), nullable=False, default=ACCOUNT_TYPE_DEMO)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    """Preparado para varias contas. Hoje so uma fica ativa por vez."""

    # --- onde o terminal esta -------------------------------------------
    #
    # Endereco da ponte RPyC quando o MetaTrader roda sob Wine em outro
    # container. Vazio = comportamento antigo (pacote `MetaTrader5` local,
    # so no Windows). Fica AQUI, e nao so no `.env`, porque "com qual
    # terminal eu falo" e a mesma decisao que login/senha/servidor —
    # dividi-la entre banco e ambiente e o que produz "salvei no painel e
    # nao mudou nada".
    bridge_host: Mapped[str | None] = mapped_column(String(200), nullable=True)
    bridge_port: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # --- resultado do ultimo teste ---------------------------------------
    #
    # `last_test_at` e `last_success_at` sao campos DIFERENTES de proposito.
    # "Testei agora e falhou" e "funcionou tres dias atras" sao fatos
    # distintos, e juntar os dois num campo so faria a tela dizer
    # "conectado" sobre uma configuracao que parou de funcionar.
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_test_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)

    def __repr__(self) -> str:
        # Sem senha, sem cifrado: `repr` acaba em log com facilidade.
        return f"Mt5Credential(login={self.login!r}, server={self.server!r})"
