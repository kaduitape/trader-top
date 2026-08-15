"""Fluxo OpenID Connect do Google usado pelo login web."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
from jose import JWTError, jwt

from app.core.config import Settings

GOOGLE_AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_JWKS_ENDPOINT = "https://www.googleapis.com/oauth2/v3/certs"


class GoogleOAuthError(ValueError):
    """Resposta invalida ou identidade nao verificavel no fluxo Google."""


@dataclass(frozen=True)
class GoogleIdentity:
    subject: str
    email: str


def build_authorization_url(*, settings: Settings, state: str, nonce: str) -> str:
    if not settings.google_oauth_enabled:
        raise GoogleOAuthError("Login com Google nao configurado.")
    params = {
        "client_id": settings.google_oauth_client_id,
        "redirect_uri": settings.google_oauth_redirect_uri,
        "response_type": "code",
        "scope": "openid email",
        "state": state,
        "nonce": nonce,
        "prompt": "select_account",
    }
    return f"{GOOGLE_AUTHORIZATION_ENDPOINT}?{urlencode(params)}"


def exchange_code_for_identity(
    *, settings: Settings, code: str, expected_nonce: str
) -> GoogleIdentity:
    """Troca o codigo e valida assinatura, emissor, audiencia e nonce do ID token."""
    if not settings.google_oauth_enabled:
        raise GoogleOAuthError("Login com Google nao configurado.")

    try:
        with httpx.Client(timeout=10.0) as client:
            token_response = client.post(
                GOOGLE_TOKEN_ENDPOINT,
                data={
                    "client_id": settings.google_oauth_client_id,
                    "client_secret": settings.google_oauth_client_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": settings.google_oauth_redirect_uri,
                },
            )
            token_response.raise_for_status()
            id_token = token_response.json()["id_token"]
            jwks_response = client.get(GOOGLE_JWKS_ENDPOINT)
            jwks_response.raise_for_status()
            keys = jwks_response.json()["keys"]

        header = jwt.get_unverified_header(id_token)
        key = next(item for item in keys if item.get("kid") == header.get("kid"))
        claims = jwt.decode(
            id_token,
            key,
            algorithms=["RS256"],
            audience=settings.google_oauth_client_id,
            options={"verify_iss": False},
        )
    except (httpx.HTTPError, KeyError, StopIteration, TypeError, JWTError, ValueError) as exc:
        raise GoogleOAuthError("Nao foi possivel validar a identidade Google.") from exc

    if claims.get("iss") not in {"accounts.google.com", "https://accounts.google.com"}:
        raise GoogleOAuthError("Emissor invalido no retorno do Google.")
    if claims.get("nonce") != expected_nonce:
        raise GoogleOAuthError("Nonce invalido no retorno do Google.")
    if claims.get("email_verified") is not True:
        raise GoogleOAuthError("O e-mail da conta Google nao esta verificado.")
    subject = claims.get("sub")
    email = claims.get("email")
    if not isinstance(subject, str) or not isinstance(email, str):
        raise GoogleOAuthError("Identidade Google incompleta.")
    return GoogleIdentity(subject=subject, email=email.lower())
