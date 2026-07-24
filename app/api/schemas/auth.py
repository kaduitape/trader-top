"""Schemas Pydantic da API de autenticacao. Nenhuma rota recebe payload nao
tipado — toda entrada do usuario passa por um destes schemas."""

from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=255)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in_minutes: int


class UserOut(BaseModel):
    id: int
    username: str
    email: EmailStr
    is_active: bool
    roles: list[str]

    model_config = {"from_attributes": True}
