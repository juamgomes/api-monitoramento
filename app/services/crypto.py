from pathlib import Path

from cryptography.fernet import Fernet

from app.config import DEFAULT_KEY_PATH, get_settings


class CredentialCipher:
    def __init__(self) -> None:
        self._fernet = Fernet(self._load_key())

    def _load_key(self) -> bytes:
        settings = get_settings()
        configured = settings.monitoring_encryption_key
        if configured and configured.get_secret_value():
            return configured.get_secret_value().encode("utf-8")

        key_path = Path(DEFAULT_KEY_PATH)
        key_path.parent.mkdir(parents=True, exist_ok=True)
        if key_path.exists():
            return key_path.read_bytes()

        key = Fernet.generate_key()
        key_path.write_bytes(key)
        return key

    def encrypt(self, value: str | None) -> str | None:
        if not value:
            return None
        return self._fernet.encrypt(value.encode("utf-8")).decode("utf-8")

    def decrypt(self, value: str | None) -> str | None:
        if not value:
            return None
        return self._fernet.decrypt(value.encode("utf-8")).decode("utf-8")


cipher = CredentialCipher()
