from app.core.security import hash_password
from app.database.repositories.user_repository import UserRepository


def test_create_and_fetch_user(db_session) -> None:
    repo = UserRepository(db_session)
    role = repo.get_or_create_role("ANALYST", "Perfil de analise")
    user = repo.create_user(
        username="repo_test_user",
        email="repo_test_user@example.com",
        password_hash=hash_password("s3cret!"),
        roles=[role],
    )
    db_session.commit()

    fetched = repo.get_by_username("repo_test_user")
    assert fetched is not None
    assert fetched.id == user.id
    assert [r.name for r in fetched.roles] == ["ANALYST"]


def test_get_by_username_returns_none_when_missing(db_session) -> None:
    repo = UserRepository(db_session)
    assert repo.get_by_username("does_not_exist_xyz") is None


def test_get_or_create_role_is_idempotent(db_session) -> None:
    repo = UserRepository(db_session)
    role_a = repo.get_or_create_role("VIEWER")
    role_b = repo.get_or_create_role("VIEWER")
    db_session.commit()
    assert role_a.id == role_b.id
