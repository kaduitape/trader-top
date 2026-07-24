"""Excecoes de dominio compartilhadas.

Cada camada (banco, autenticacao, e futuramente MT5/risco/execucao) deriva
suas excecoes especificas de `AppError`, permitindo tratamento uniforme na
camada de API (ver `app/api/dependencies`).
"""

from __future__ import annotations


class AppError(Exception):
    """Excecao base de todas as excecoes de dominio da aplicacao."""


class ConfigurationError(AppError):
    """Configuracao obrigatoria ausente ou invalida."""


class AuthenticationError(AppError):
    """Falha de autenticacao (credenciais invalidas ou token expirado)."""


class AuthorizationError(AppError):
    """Usuario autenticado sem permissao para a acao solicitada."""


class DatabaseUnavailableError(AppError):
    """Nao foi possivel estabelecer ou usar a conexao com o banco de dados."""


class MT5ConnectionError(AppError):
    """Nao foi possivel conectar ao terminal MetaTrader 5 apos as tentativas
    de reconexao configuradas."""


class MT5RealAccountError(AppError):
    """Uma funcao de envio de ordem (Fase 11+) foi chamada com uma conta
    que nao e demo. Bloqueio de seguranca incondicional — nunca contornado,
    nem mesmo por configuracao."""
