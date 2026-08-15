"""Criptografia simetrica das credenciais guardadas no banco.

Ate aqui as credenciais do MetaTrader viviam SO no `.env` do host Windows —
decisao deliberada de nao ter senha de corretora no banco nem no navegador.
Guardar no banco amplia a superficie: quem le o banco (backup, replica,
dump de suporte) passa a ter o material. A criptografia existe para que ler
o banco nao baste.

O que ela protege e o que nao protege, dito na cara:

- **Protege** contra vazamento do BANCO isolado: dump, backup, replica,
  print de tabela. Sem a chave, `password_encrypted` e ruido.
- **Nao protege** contra quem tem acesso ao servidor da aplicacao. Quem le o
  `.env` le a chave, e quem le a chave abre tudo. Isso nao e falha do
  desenho: e o limite de qualquer criptografia simetrica cuja chave precisa
  estar disponivel ao processo que descriptografa.

Fernet (AES-128-CBC + HMAC-SHA256, do `cryptography`) porque ele autentica
o texto cifrado: adulterar o campo no banco produz erro, e nao uma senha
diferente enviada em silencio para a corretora.
"""

from __future__ import annotations

import base64
import hashlib
import logging
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)


class CredentialCryptoError(RuntimeError):
    """Nao foi possivel cifrar/decifrar. Nunca inclui o material em claro."""


def _derive_key(material: str) -> bytes:
    """Chave Fernet valida a partir de um segredo qualquer.

    Fernet exige 32 bytes em base64url. Derivar por SHA-256 aceita um
    segredo de qualquer formato sem exigir que o operador saiba gerar uma
    chave no formato exato — o que, na pratica, levaria a chave fraca ou a
    campo em branco.
    """
    return base64.urlsafe_b64encode(hashlib.sha256(material.encode("utf-8")).digest())


@lru_cache(maxsize=4)
def _cipher(material: str) -> Fernet:
    return Fernet(_derive_key(material))


def _key_material() -> str:
    from app.core.config import get_settings

    settings = get_settings()
    # Chave dedicada quando existir; senao, derivada do segredo da aplicacao.
    # A queda evita que uma instalacao ja em uso pare de subir por falta de
    # uma variavel nova — e continua fora do codigo-fonte, que e a exigencia
    # real. Trocar qualquer uma das duas invalida o que ja estava cifrado.
    material = getattr(settings, "credentials_encryption_key", None)
    return material or settings.app_secret_key


def encrypt_secret(plaintext: str) -> str:
    if not plaintext:
        return ""
    try:
        return _cipher(_key_material()).encrypt(plaintext.encode("utf-8")).decode("ascii")
    except Exception as exc:  # pragma: no cover - falha de biblioteca
        raise CredentialCryptoError("nao foi possivel cifrar a credencial") from exc


def decrypt_secret(ciphertext: str) -> str:
    """Devolve o texto em claro. Erro NUNCA vaza o conteudo."""
    if not ciphertext:
        return ""
    try:
        return _cipher(_key_material()).decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        # Chave trocada ou registro adulterado. A mensagem e generica de
        # proposito: detalhar aqui viraria pista para quem tenta adivinhar.
        raise CredentialCryptoError(
            "credencial guardada nao pode ser lida com a chave atual — "
            "cadastre a senha novamente"
        ) from exc
    except Exception as exc:
        raise CredentialCryptoError("nao foi possivel ler a credencial") from exc


MASK = "•" * 12


def mask_secret(ciphertext: str | None) -> str | None:
    """O que a tela pode mostrar: existe ou nao existe, nunca o valor."""
    return MASK if ciphertext else None
