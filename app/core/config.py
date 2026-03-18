from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    """
    All configuration is read from environment variables (set in devenv.nix
    for local dev, and from AWS Secrets Manager / ECS task env for production).
    Pydantic will raise a clear error on startup if any required field is missing.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # ── Database ──────────────────────────────────────────────────────────────

    # Full SQLAlchemy connection string, e.g.
    # postgresql+psycopg://postgres:password@localhost:5432/alter_event_tracker
    database_url: str

    # ── JWT ───────────────────────────────────────────────────────────────────

    # Random secret used to sign and verify tokens. Must be kept private -
    # anyone who knows this value can forge valid tokens.
    # Generate with: openssl rand -hex 32
    secret_key: str

    # Signing algorithm. HS256 (HMAC-SHA256) is a symmetric algorithm -
    # the same key is used to both sign and verify.
    algorithm: str = "HS256"

    # How long a token stays valid after it is issued.
    # After this window the token is rejected and the user must log in again.
    access_token_expire_minutes: int = 720

    # ── App meta ──────────────────────────────────────────────────────────────

    # Displayed in the OpenAPI docs title and health check response.
    app_name: str = "Alter Event Tracker"

    # Port uvicorn listens on. Referenced in the dev-backend devenv script.
    backend_port: int = 8000

    # When True, SQLAlchemy logs every SQL statement to stdout.
    # Never enable in production.
    debug: bool = False

    # ── Mazmo External API ────────────────────────────────────────────────────

    # Base URL of the Mazmo API. Extracted here so it can be overridden in
    # tests or if Mazmo ever changes their domain.
    mazmo_base_url: str = "https://prod.mazmoapi.net"

    # The community slug that appears in the thread URL path.
    mazmo_community_slug: str = "eventos-reuniones-argentina"

    # The human-readable slug for the specific event thread.
    mazmo_thread_slug: str = "alter-cordoba-nos-ponemos-la-10-edicion"

    # The numeric ID that Mazmo appends to the slug, e.g. the URL ends in
    # "alter-cordoba-nos-ponemos-la-10-edicion-4217". Set via env var.
    mazmo_thread_id: int

    # Maximum number of user IDs sent per /users request.
    # Keeps query strings short enough to avoid 414 URI Too Long errors.
    mazmo_user_batch_size: int = 100

    # Timeout in seconds for outbound HTTP calls to Mazmo.
    # If Mazmo doesn't respond within this window the request is aborted.
    mazmo_request_timeout: float = 15.0


@lru_cache
def get_settings() -> Settings:
    """Cached singleton – avoids re-reading env on every request."""
    return Settings()
