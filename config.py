"""
Configuration for SURAKSHAI Personnel Welfare Intelligence System
"""

import os
from urllib.parse import parse_qs, urlsplit
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

MODELS_DIR = os.path.join(BASE_DIR, "models")
KNOWLEDGE_BASE_DIR = os.path.join(BASE_DIR, "knowledge_base")


# Welfare recommendation / RAG configuration
EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50
TOP_K_RETRIEVAL = 3


def _as_bool(value, *, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in {'1', 'true', 'yes', 'on'}


def get_app_env():
    environment = os.getenv('APP_ENV', os.getenv('SURAKSHAI_ENV', 'development')).strip().lower()
    if environment not in {'development', 'test', 'production'}:
        raise ValueError('APP_ENV must be development, test, or production')
    return environment


def is_development_seed_enabled():
    return get_app_env() == 'development' and _as_bool(os.getenv('SURAKSHAI_ENABLE_DEV_USER_SEED'))


def validate_synthetic_seed_configuration(*, reset=False):
    """Fail closed unless synthetic seeding targets an explicitly named dev database."""
    environment = get_app_env()
    if environment == 'production':
        raise ValueError('Synthetic staging data is disabled in production')
    if environment != 'development':
        raise ValueError('Synthetic staging data requires APP_ENV=development')
    if os.getenv('SURAKSHAI_ENABLE_SYNTHETIC_SEED') != '1':
        raise ValueError('Set SURAKSHAI_ENABLE_SYNTHETIC_SEED=1 to enable synthetic development seeding')
    database_name = get_database_name()
    if not any(
        marker in database_name.lower().replace('-', '_').split('_')
        for marker in ('dev', 'development', 'staging', 'stage')
    ):
        raise ValueError('MONGODB_DATABASE must clearly identify a development or staging database')
    if reset and os.getenv('SURAKSHAI_RESET_SYNTHETIC_DATA') != '1':
        raise ValueError('Set SURAKSHAI_RESET_SYNTHETIC_DATA=1 to reset synthetic staging data')
    return database_name


def get_debug_mode():
    return get_app_env() != 'production' and _as_bool(os.getenv('DEBUG'), default=False)


def get_cors_allowed_origins():
    configured = [origin.strip() for origin in os.getenv('CORS_ALLOWED_ORIGINS', '').split(',') if origin.strip()]
    if configured:
        if '*' in configured:
            raise ValueError('CORS_ALLOWED_ORIGINS must not contain a wildcard')
        return configured
    if get_app_env() == 'development':
        return ['http://localhost:8083', 'http://127.0.0.1:8083']
    return []


def get_max_request_size_bytes():
    try:
        value = int(os.getenv('MAX_REQUEST_SIZE_BYTES', str(1024 * 1024)))
    except ValueError as error:
        raise ValueError('MAX_REQUEST_SIZE_BYTES must be an integer') from error
    if not 1024 <= value <= 10 * 1024 * 1024:
        raise ValueError('MAX_REQUEST_SIZE_BYTES must be between 1 KB and 10 MB')
    return value


def get_retention_days(name):
    """Return a governance placeholder; None means no approved period is set."""
    value = os.getenv(name)
    if value is None or not value.strip():
        return None
    try:
        days = int(value)
    except ValueError as error:
        raise ValueError(f'{name} must be a positive integer when configured') from error
    if days < 1:
        raise ValueError(f'{name} must be a positive integer when configured')
    return days


def get_jwt_access_token_expires_minutes():
    try:
        minutes = int(os.getenv('JWT_ACCESS_TOKEN_EXPIRES_MINUTES', '60'))
    except ValueError as error:
        raise ValueError('JWT_ACCESS_TOKEN_EXPIRES_MINUTES must be an integer') from error
    if not 1 <= minutes <= 60:
        raise ValueError('JWT_ACCESS_TOKEN_EXPIRES_MINUTES must be between 1 and 60')
    return minutes


def _bounded_positive_int(name, default, maximum):
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as error:
        raise ValueError(f'{name} must be an integer') from error
    if not 1 <= value <= maximum:
        raise ValueError(f'{name} must be between 1 and {maximum}')
    return value


def validate_production_configuration():
    if get_app_env() != 'production':
        return
    jwt_secret = os.getenv('JWT_SECRET_KEY', '')
    pseudonym_secret = os.getenv('PSEUDONYMIZATION_SECRET', '')
    placeholders = ('replace-with', 'change-me', 'changeme', 'example', 'development')
    if (
        len(jwt_secret) < 32
        or len(pseudonym_secret) < 32
        or jwt_secret == pseudonym_secret
        or any(marker in jwt_secret.lower() or marker in pseudonym_secret.lower() for marker in placeholders)
    ):
        raise ValueError('Production JWT and pseudonymization secrets must be distinct, random values of at least 32 characters')
    algorithm = os.getenv('JWT_ALGORITHM', 'HS256')
    if algorithm not in {'HS256', 'HS384', 'HS512'}:
        raise ValueError('JWT_ALGORITHM must be HS256, HS384, or HS512')
    origins = get_cors_allowed_origins()
    if not origins or any(not origin.startswith('https://') for origin in origins):
        raise ValueError('Production CORS_ALLOWED_ORIGINS must list explicit HTTPS origins')
    mongo_uri = get_mongodb_uri()
    if not mongo_uri:
        raise ValueError('MONGODB_URI must be configured in production')
    if not mongo_uri.startswith('mongodb+srv://') and not _as_bool(os.getenv('MONGODB_TLS')):
        raise ValueError('MONGODB_TLS must be enabled for production MongoDB connections')
    options = parse_qs(urlsplit(mongo_uri).query)
    if any(value.lower() == 'false' for value in options.get('tls', []) + options.get('ssl', [])):
        raise ValueError('Production MongoDB URI must not disable TLS')
    if not os.getenv('JWT_ISSUER', 'surakshai-api').strip() or not os.getenv('JWT_AUDIENCE', 'surakshai-client').strip():
        raise ValueError('JWT_ISSUER and JWT_AUDIENCE must be configured in production')
    get_jwt_access_token_expires_minutes()


# Welfare alert workflow policy
WELFARE_ALERT_PERSISTENCE_WINDOW_DAYS = int(
    os.getenv("WELFARE_ALERT_PERSISTENCE_WINDOW_DAYS", "7")
)

WELFARE_ALERT_ELEVATED_OBSERVATIONS = int(
    os.getenv("WELFARE_ALERT_ELEVATED_OBSERVATIONS", "2")
)

WELFARE_ALERT_HIGH_OBSERVATIONS = int(
    os.getenv("WELFARE_ALERT_HIGH_OBSERVATIONS", "2")
)

WELFARE_ALERT_COOLDOWN_DAYS = int(
    os.getenv("WELFARE_ALERT_COOLDOWN_DAYS", "7")
)


def get_mongodb_uri():
    return os.getenv("MONGODB_URI")


def get_database_name():
    database_name = os.getenv(
        "MONGODB_DATABASE",
        "personnel_welfare"
    ).strip()

    if not database_name:
        raise ValueError("MONGODB_DATABASE must not be empty")

    if len(database_name.encode("utf-8")) > 38:
        raise ValueError("MONGODB_DATABASE must be 38 bytes or fewer")

    return database_name


def get_mongodb_timeout_ms():
    timeout = int(os.getenv("MONGODB_TIMEOUT_MS", "3000").strip())
    if not 100 <= timeout <= 30000:
        raise ValueError('MONGODB_TIMEOUT_MS must be between 100 and 30000')
    return timeout


def get_mongodb_tls():
    value = os.getenv('MONGODB_TLS')
    if value is None:
        return None
    return _as_bool(value)


def get_jwt_secret_key():
    return os.getenv("JWT_SECRET_KEY")


def get_pseudonymization_secret():
    return os.getenv("PSEUDONYMIZATION_SECRET")


# Governance placeholders only. No deletion job consumes these values.
WELLNESS_RETENTION_DAYS = get_retention_days('WELLNESS_RETENTION_DAYS')
RISK_RETENTION_DAYS = get_retention_days('RISK_RETENTION_DAYS')
AUDIT_RETENTION_DAYS = get_retention_days('AUDIT_RETENTION_DAYS')
LOGIN_RATE_LIMIT = _bounded_positive_int('LOGIN_RATE_LIMIT', 10, 100)
ADMIN_USER_RATE_LIMIT = _bounded_positive_int('ADMIN_USER_RATE_LIMIT', 20, 200)
HRMS_SYNC_RATE_LIMIT = _bounded_positive_int('HRMS_SYNC_RATE_LIMIT', 10, 100)
RATE_LIMIT_WINDOW_SECONDS = _bounded_positive_int('RATE_LIMIT_WINDOW_SECONDS', 60, 3600)
