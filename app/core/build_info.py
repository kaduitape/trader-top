"""Que versao do codigo este processo esta rodando.

O conector Windows e o servidor web sao processos separados, em maquinas
que podem ser separadas, cada um com a sua copia do repositorio. Nada
garante que estejam na mesma versao — e quando nao estao, o sintoma e
sempre o mesmo: "consertei aquilo e continua igual".

Foi exatamente o que aconteceu com as quedas do conector. As correcoes de
supervisao existiam no repositorio e nao na maquina que roda o worker, e
nao havia como perceber isso olhando o painel.

A leitura e feita UMA vez por processo. `git` pode nao existir na maquina
do worker (instalacao por copia de pasta), entao a ausencia dele nao e
erro: cai para a versao do pacote, e no pior caso para "desconhecida".
"""

from __future__ import annotations

import subprocess
from functools import lru_cache
from pathlib import Path

UNKNOWN = "desconhecida"


def _git_short_sha() -> str | None:
    raiz = Path(__file__).resolve().parents[2]
    if not (raiz / ".git").exists():
        return None
    try:
        saida = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=raiz,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    sha = saida.stdout.strip()
    return sha or None


def _package_version() -> str | None:
    try:
        from importlib.metadata import PackageNotFoundError, version
    except ImportError:  # pragma: no cover - Python muito antigo
        return None
    try:
        return version("mt5-ai-scalper")
    except PackageNotFoundError:
        return None


@lru_cache(maxsize=1)
def code_version() -> str:
    """Identificador curto e estavel do codigo em execucao."""
    return _git_short_sha() or _package_version() or UNKNOWN


def versions_match(a: str | None, b: str | None) -> bool:
    """Duas versoes sao comparaveis e iguais?

    Desconhecido nunca "bate" com nada: alertar sem certeza e melhor que
    silenciar uma divergencia real, porque o custo de investigar um alerta
    falso e menor que o de procurar um bug ja corrigido.
    """
    if not a or not b:
        return False
    if UNKNOWN in (a, b):
        return False
    return a == b
