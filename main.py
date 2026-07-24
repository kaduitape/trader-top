"""Ponto de entrada da aplicacao. Uso: `uvicorn main:app --reload`."""

from app.api.app import app

__all__ = ["app"]
