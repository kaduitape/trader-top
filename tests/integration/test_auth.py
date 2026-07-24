from app.core.security import hash_password
from app.database.models.audit_log import AuditLog
from app.database.repositories.user_repository import UserRepository


def _create_user(db_session, username: str, password: str, role_name: str = "ADMIN"):
    repo = UserRepository(db_session)
    role = repo.get_or_create_role(role_name)
    user = repo.create_user(
        username=username,
        email=f"{username}@example.com",
        password_hash=hash_password(password),
        roles=[role],
    )
    db_session.commit()
    return user


def test_login_with_valid_credentials_returns_token(client, db_session) -> None:
    _create_user(db_session, "auth_test_user", "correct-password")

    response = client.post(
        "/api/auth/login",
        json={"username": "auth_test_user", "password": "correct-password"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]


def test_login_with_invalid_password_returns_401(client, db_session) -> None:
    _create_user(db_session, "auth_test_user_2", "correct-password")

    response = client.post(
        "/api/auth/login",
        json={"username": "auth_test_user_2", "password": "wrong-password"},
    )

    assert response.status_code == 401


def test_login_failure_is_audited(client, db_session) -> None:
    _create_user(db_session, "auth_test_user_3", "correct-password")

    client.post(
        "/api/auth/login",
        json={"username": "auth_test_user_3", "password": "wrong-password"},
    )

    failure_logs = (
        db_session.query(AuditLog)
        .filter(AuditLog.action == "login", AuditLog.result == "FAILURE")
        .all()
    )
    assert len(failure_logs) >= 1


def test_me_requires_valid_token(client, db_session) -> None:
    _create_user(db_session, "auth_test_user_4", "correct-password")

    login_response = client.post(
        "/api/auth/login",
        json={"username": "auth_test_user_4", "password": "correct-password"},
    )
    token = login_response.json()["access_token"]

    response = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["username"] == "auth_test_user_4"
    assert response.json()["roles"] == ["ADMIN"]


def test_me_without_token_is_rejected(client) -> None:
    response = client.get("/api/auth/me")
    assert response.status_code == 401
