"""Runtime configuration loaded from environment / .env."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

from . import constants as C

load_dotenv()


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    return int(raw) if raw else default


def _env_float(name: str, default: float) -> float:
    raw = _env(name)
    return float(raw) if raw else default


@dataclass
class ProxyConfig:
    """Proxy pool settings."""

    urls: list[str] = field(default_factory=list)
    health_check_url: str = "https://api.ipify.org?format=json"
    max_failures: int = 3

    @property
    def enabled(self) -> bool:
        return bool(self.urls)


@dataclass
class Settings:
    # Backend
    supabase_url: str = C.SUPABASE_URL
    supabase_key: str = C.SUPABASE_PUBLISHABLE_KEY
    conso_web: str = C.CONSO_WEB

    # Concurrency
    concurrency: int = 4
    max_concurrency: int = 16
    request_timeout: float = 30.0
    max_retries: int = 3

    # Fingerprint / transport
    impersonate: str = "chrome"
    user_agent: str = ""

    # Identity generation
    email_domain: str = ""
    password_length: int = 16
    display_name_prefix: str = "user"

    # Anti-abuse pacing
    min_delay_seconds: float = 1.5
    max_delay_seconds: float = 6.0

    # Paths
    data_dir: str = "data"
    logs_dir: str = "logs"

    # Proxy
    proxy: ProxyConfig = field(default_factory=ProxyConfig)

    @classmethod
    def from_env(cls) -> "Settings":
        proxy_urls = [u for u in _env("PROXY_URLS").split(",") if u.strip()]
        return cls(
            supabase_url=_env("SUPABASE_URL", C.SUPABASE_URL),
            supabase_key=_env("SUPABASE_KEY", C.SUPABASE_PUBLISHABLE_KEY),
            conso_web=_env("CONSO_WEB", C.CONSO_WEB),
            concurrency=_env_int("CONCURRENCY", 4),
            max_concurrency=_env_int("MAX_CONCURRENCY", 16),
            request_timeout=_env_float("REQUEST_TIMEOUT", 30.0),
            max_retries=_env_int("MAX_RETRIES", 3),
            impersonate=_env("IMPERSONATE", "chrome"),
            user_agent=_env("USER_AGENT"),
            email_domain=_env("EMAIL_DOMAIN"),
            password_length=_env_int("PASSWORD_LENGTH", 16),
            display_name_prefix=_env("DISPLAY_NAME_PREFIX", "user"),
            min_delay_seconds=_env_float("MIN_DELAY_SECONDS", 1.5),
            max_delay_seconds=_env_float("MAX_DELAY_SECONDS", 6.0),
            data_dir=_env("DATA_DIR", "data"),
            logs_dir=_env("LOGS_DIR", "logs"),
            proxy=ProxyConfig(urls=proxy_urls),
        )
