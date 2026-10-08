import hashlib
import hmac
import secrets
from dataclasses import dataclass


@dataclass(frozen=True)
class GeneratedApiKey:
    plaintext: str
    prefix: str
    secret_hash: str


def hash_api_key(api_key: str, hash_secret: str) -> str:
    return hmac.new(
        hash_secret.encode("utf-8"),
        api_key.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def generate_api_key(hash_secret: str) -> GeneratedApiKey:
    prefix = f"tg_{secrets.token_hex(6)}"
    plaintext = f"{prefix}_{secrets.token_urlsafe(32)}"
    return GeneratedApiKey(
        plaintext=plaintext,
        prefix=prefix,
        secret_hash=hash_api_key(plaintext, hash_secret),
    )


def extract_api_key_prefix(api_key: str) -> str | None:
    parts = api_key.split("_", maxsplit=2)
    if len(parts) != 3 or parts[0] != "tg" or len(parts[1]) != 12 or not parts[2]:
        return None
    return f"tg_{parts[1]}"
