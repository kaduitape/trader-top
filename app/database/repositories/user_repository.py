"""Repositorio de usuarios. Encapsula todo acesso a tabela `users` — servicos
e rotas nunca escrevem queries SQLAlchemy diretamente sobre `User`."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models.user import Role, User


class UserRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_username(self, username: str) -> User | None:
        stmt = select(User).where(User.username == username)
        return self._session.execute(stmt).scalar_one_or_none()

    def get_by_email(self, email: str) -> User | None:
        stmt = select(User).where(User.email == email)
        return self._session.execute(stmt).scalar_one_or_none()

    def get_by_id(self, user_id: int) -> User | None:
        return self._session.get(User, user_id)

    def get_or_create_role(self, name: str, description: str | None = None) -> Role:
        stmt = select(Role).where(Role.name == name)
        role = self._session.execute(stmt).scalar_one_or_none()
        if role is None:
            role = Role(name=name, description=description)
            self._session.add(role)
            self._session.flush()
        return role

    def create_user(
        self,
        *,
        username: str,
        email: str,
        password_hash: str,
        roles: list[Role] | None = None,
    ) -> User:
        user = User(
            username=username,
            email=email,
            password_hash=password_hash,
            roles=roles or [],
        )
        self._session.add(user)
        self._session.flush()
        return user
