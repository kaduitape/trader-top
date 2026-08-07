"""Versao do codigo em execucao.

Serve a um proposito so: tornar visivel que o conector Windows e o servidor
web podem estar em versoes diferentes.

A primeira versao disto usava o sha do git e estava errada de um jeito que
so aparecia em producao: `.git/` esta no `.dockerignore`, entao o painel em
container caia para a versao do pacote enquanto o conector no Windows
devolvia um sha. Os dois NUNCA batiam, e o alarme ficava ligado para
sempre — dizendo o contrario da verdade.

Por isso os testes abaixo cobrem principalmente as duas coisas que
produziam falso alarme: dependencia do ambiente e fim de linha.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

import app.core.build_info as build_info
from app.core.build_info import UNKNOWN, code_version, versions_match


@pytest.fixture(autouse=True)
def _sem_cache():
    code_version.cache_clear()
    yield
    code_version.cache_clear()


def _digital_de(tmp_path: Path, arquivos: dict[str, bytes]) -> str:
    """Calcula a digital de uma arvore sintetica."""
    raiz = tmp_path / "projeto"
    fonte = raiz / "app"
    for nome, conteudo in arquivos.items():
        caminho = fonte / nome
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_bytes(conteudo)

    original_raiz, original_fonte = build_info._ROOT, build_info._SOURCE_DIR
    build_info._ROOT, build_info._SOURCE_DIR = raiz, fonte
    try:
        code_version.cache_clear()
        return code_version()
    finally:
        build_info._ROOT, build_info._SOURCE_DIR = original_raiz, original_fonte
        code_version.cache_clear()


def test_it_always_answers_something() -> None:
    assert code_version()


def test_the_answer_is_stable_within_the_process() -> None:
    assert code_version() == code_version()


def test_it_does_not_depend_on_git_being_present() -> None:
    """A arvore sintetica nao tem `.git` nenhum — e ainda assim responde.

    Era exatamente isto que faltava: o painel roda em container sem
    repositorio, e nao pode por isso reportar uma versao incomparavel.
    """
    digital = _digital_de(Path(__file__).parent / "_tmp_git", {"m.py": b"x = 1\n"})

    assert digital != UNKNOWN
    assert len(digital) == 16


def test_line_endings_do_not_change_the_fingerprint(tmp_path) -> None:
    """O git no Windows converte LF para CRLF na checagem. Sem normalizar, o
    MESMO commit daria digitais diferentes nos dois sistemas — recriando o
    falso alarme que este modulo existe para eliminar."""
    unix = _digital_de(tmp_path / "a", {"m.py": b"x = 1\ny = 2\n"})
    windows = _digital_de(tmp_path / "b", {"m.py": b"x = 1\r\ny = 2\r\n"})

    assert unix == windows


def test_different_code_gives_a_different_fingerprint(tmp_path) -> None:
    antes = _digital_de(tmp_path / "a", {"m.py": b"x = 1\n"})
    depois = _digital_de(tmp_path / "b", {"m.py": b"x = 2\n"})

    assert antes != depois


def test_the_file_name_is_part_of_the_fingerprint(tmp_path) -> None:
    """Mover codigo de arquivo e uma mudanca de versao."""
    a = _digital_de(tmp_path / "a", {"um.py": b"x = 1\n"})
    b = _digital_de(tmp_path / "b", {"dois.py": b"x = 1\n"})

    assert a != b


def test_a_new_file_changes_the_fingerprint(tmp_path) -> None:
    antes = _digital_de(tmp_path / "a", {"m.py": b"x = 1\n"})
    depois = _digital_de(tmp_path / "b", {"m.py": b"x = 1\n", "n.py": b"y = 2\n"})

    assert antes != depois


def test_pycache_is_ignored(tmp_path) -> None:
    """`.pyc` e artefato local: presenca dele nao pode virar "versao
    diferente"."""
    limpo = _digital_de(tmp_path / "a", {"m.py": b"x = 1\n"})
    sujo = _digital_de(
        tmp_path / "b", {"m.py": b"x = 1\n", "__pycache__/m.cpython-313.py": b"lixo"}
    )

    assert limpo == sujo


def test_a_tree_without_sources_is_unknown(tmp_path) -> None:
    raiz = tmp_path / "vazio"
    raiz.mkdir()
    original_raiz, original_fonte = build_info._ROOT, build_info._SOURCE_DIR
    build_info._ROOT, build_info._SOURCE_DIR = raiz, raiz / "app"
    try:
        code_version.cache_clear()
        assert code_version() == UNKNOWN
    finally:
        build_info._ROOT, build_info._SOURCE_DIR = original_raiz, original_fonte
        code_version.cache_clear()


def test_the_fingerprint_is_short_enough_for_a_screen() -> None:
    assert len(code_version()) <= 20


def test_it_is_a_real_digest_not_a_timestamp() -> None:
    """Digital tem que ser funcao do conteudo. Se fosse hora de inicio, dois
    processos do mesmo codigo nunca bateriam."""
    assert set(code_version()) <= set(hashlib.blake2b().hexdigest())


# --- comparacao ------------------------------------------------------------


def test_equal_versions_match() -> None:
    assert versions_match("abc1234", "abc1234") is True


def test_different_versions_do_not_match() -> None:
    assert versions_match("abc1234", "def5678") is False


def test_an_unknown_version_never_matches() -> None:
    assert versions_match(UNKNOWN, UNKNOWN) is False
    assert versions_match("abc1234", UNKNOWN) is False


def test_a_missing_version_never_matches() -> None:
    assert versions_match(None, "abc1234") is False
    assert versions_match("abc1234", "") is False
