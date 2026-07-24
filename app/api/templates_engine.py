"""Instancia unica de `Jinja2Templates`, compartilhada por todas as rotas
HTML (login e dashboard) — um unico ponto de configuracao do diretorio de
templates."""

from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

TEMPLATES_DIR = Path(__file__).parent / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
