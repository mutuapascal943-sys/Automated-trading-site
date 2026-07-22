import os
from pathlib import Path
from decouple import config

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = config('DJANGO_SECRET_KEY', default='')
if not SECRET_KEY:
    raise ValueError(
        'DJANGO_SECRET_KEY environment variable is required. '
        'Generate one with: python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"'
    )

DEBUG = config('DJANGO_DEBUG', default=False, cast=bool)

ALLOWED_HOSTS = config('DJANGO_ALLOWED_HOSTS', default='localhost,127.0.0.1').split(',')

INSTALLED_APPS = [
    'daphne',
    'channels',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'corsheaders',
    'trading_app',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'forex_ai_pro.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'forex_ai_pro.wsgi.application'
ASGI_APPLICATION = 'forex_ai_pro.asgi.application'

REDIS_URL = config('REDIS_URL', default='')

_use_redis = False
if REDIS_URL:
    try:
        import redis as _redis_check
        _check = _redis_check.from_url(REDIS_URL, socket_connect_timeout=2)
        _check.ping()
        _use_redis = True
    except Exception:
        pass

if _use_redis:
    CHANNEL_LAYERS = {
        'default': {
            'BACKEND': 'channels_redis.core.RedisChannelLayer',
            'CONFIG': {
                'hosts': [REDIS_URL],
                'capacity': 1500,
                'expiry': 10,
            },
        },
    }
else:
    CHANNEL_LAYERS = {
        'default': {
            'BACKEND': 'channels.layers.InMemoryChannelLayer',
        },
    }

# Database - supports SQLite (dev) and PostgreSQL (prod)
DATABASE_URL = config('DATABASE_URL', default='sqlite:///db.sqlite3')
if DATABASE_URL.startswith('postgres'):
    import re
    match = re.match(r'postgres://(.+):(.+)@(.+):(\d+)/(.+)', DATABASE_URL)
    if match:
        DATABASES = {
            'default': {
                'ENGINE': 'django.db.backends.postgresql',
                'NAME': match.group(5),
                'USER': match.group(1),
                'PASSWORD': match.group(2),
                'HOST': match.group(3),
                'PORT': match.group(4),
                'CONN_MAX_AGE': 600,
                'OPTIONS': {
                    'pool': True,
                },
            }
        }
    else:
        DATABASES = {
            'default': {
                'ENGINE': 'django.db.backends.sqlite3',
                'NAME': BASE_DIR / 'db.sqlite3',
            }
        }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }

# Cache - Redis for production, local-memory for development
CACHE_URL = config('CACHE_URL', default='')
_use_cache_redis = False
if CACHE_URL and 'redis' in CACHE_URL:
    try:
        import redis as _redis_lib
        _test_conn = _redis_lib.from_url(CACHE_URL, socket_connect_timeout=2)
        _test_conn.ping()
        _use_cache_redis = True
    except Exception:
        pass

if _use_cache_redis:
    try:
        import django_redis
        CACHES = {
            'default': {
                'BACKEND': 'django_redis.cache.RedisCache',
                'LOCATION': CACHE_URL,
                'OPTIONS': {
                    'CLIENT_CLASS': 'django_redis.client.DefaultClient',
                    'CONNECTION_POOL_CLASS': 'redis.BlockingConnectionPool',
                    'CONNECTION_POOL_CLASS_KWARGS': {
                        'max_connections': 50,
                        'timeout': 20,
                    },
                    'MAX_CONNECTIONS': 1000,
                    'PICKLE_VERSION': -1,
                },
                'KEY_PREFIX': 'forex_ai_pro',
                'TIMEOUT': 300,
            }
        }
    except ImportError:
        CACHES = {
            'default': {
                'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
                'LOCATION': 'forex-ai-pro-cache',
                'TIMEOUT': 300,
            }
        }
else:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'forex-ai-pro-cache',
            'TIMEOUT': 300,
        }
    }

# Session - use cache for performance, fall back to DB for reliability
if _use_cache_redis:
    SESSION_ENGINE = 'django.contrib.sessions.backends.cache'
    SESSION_CACHE_ALIAS = 'default'
else:
    SESSION_ENGINE = 'django.contrib.sessions.backends.db'

AUTH_PASSWORD_HASHERS = [
    'django.contrib.auth.hashers.BCryptPasswordHasher',
    'django.contrib.auth.hashers.PBKDF2PasswordHasher',
    'django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher',
    'django.contrib.auth.hashers.Argon2PasswordHasher',
]

AUTH_USER_MODEL = 'trading_app.User'

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 8}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'

MEDIA_URL = 'media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'dashboard'
LOGOUT_REDIRECT_URL = 'login'

SESSION_EXPIRE_AT_BROWSER_CLOSE = False
SESSION_COOKIE_AGE = 1209600
SESSION_SAVE_EVERY_REQUEST = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SECURE = not DEBUG

# Security headers
SECURE_BROWSER_XSS_FILTER = not DEBUG
SECURE_CONTENT_TYPE_NOSNIFF = not DEBUG
X_FRAME_OPTIONS = 'DENY'
SECURE_HSTS_SECONDS = 31536000 if not DEBUG else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG
SECURE_HSTS_PRELOAD = not DEBUG

# Email Configuration
if not DEBUG and config('EMAIL_HOST_USER', default=''):
    EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
    EMAIL_HOST = config('EMAIL_HOST', default='smtp.gmail.com')
    EMAIL_PORT = config('EMAIL_PORT', default=587, cast=int)
    EMAIL_HOST_USER = config('EMAIL_HOST_USER', default='')
    EMAIL_HOST_PASSWORD = config('EMAIL_HOST_PASSWORD', default='')
    EMAIL_USE_TLS = config('EMAIL_USE_TLS', default=True, cast=bool)
elif config('EMAIL_FILE_BACKEND', default=False, cast=bool):
    EMAIL_BACKEND = 'django.core.mail.backends.filebased.EmailBackend'
    EMAIL_FILE_PATH = BASE_DIR / 'sent_emails'
else:
    EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
DEFAULT_FROM_EMAIL = config('DEFAULT_FROM_EMAIL', default='Forex AI Pro <noreply@forexaipro.com>')

# OTP Settings
OTP_EXPIRY_SECONDS = config('OTP_EXPIRY_SECONDS', default=300, cast=int)

# Rate Limiting
MAX_LOGIN_ATTEMPTS = config('MAX_LOGIN_ATTEMPTS', default=5, cast=int)
RATE_LIMIT_REQUESTS = config('RATE_LIMIT_REQUESTS', default=100, cast=int)
RATE_LIMIT_WINDOW = config('RATE_LIMIT_WINDOW', default=60, cast=int)

# REST Framework
REST_FRAMEWORK = {
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'anon': f'{RATE_LIMIT_REQUESTS}/m',
        'user': f'{RATE_LIMIT_REQUESTS * 2}/m',
    },
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.SessionAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 50,
}

# CORS
CORS_ALLOWED_ORIGINS = [
    'http://localhost:8000',
    'http://127.0.0.1:8000',
]
CORS_ALLOW_CREDENTIALS = True

# Celery / Async Task Queue
CELERY_BROKER_URL = config('CELERY_BROKER_URL', default='')
CELERY_RESULT_BACKEND = CELERY_BROKER_URL
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = 'UTC'

from celery.schedules import crontab
CELERY_BEAT_SCHEDULE = {
    'run-bot-cycle': {
        'task': 'trading_app.tasks.run_bot_cycle',
        'schedule': crontab(minute='*/5'),
    },
    'monitor-positions': {
        'task': 'trading_app.tasks.monitor_live_positions',
        'schedule': crontab(minute='*/1'),
    },
    'sync-live-trades': {
        'task': 'trading_app.tasks.sync_live_trades',
        'schedule': crontab(minute='*/5'),
    },
    'reset-daily-trade-counts': {
        'task': 'trading_app.tasks.reset_daily_trade_counts',
        'schedule': crontab(hour=0, minute=0),
    },
    'cleanup-expired-otps': {
        'task': 'trading_app.tasks.cleanup_expired_otps',
        'schedule': crontab(hour='*/1'),
    },
}

# Logging Configuration
LOG_DIR = BASE_DIR / 'logs'
LOG_DIR.mkdir(exist_ok=True)

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '[{asctime}] {levelname} {name} {message}',
            'style': '{',
        },
    },
    'filters': {
        'require_debug_true': {
            '()': 'django.utils.log.RequireDebugTrue',
        },
    },
    'handlers': {
        'console': {
            'level': 'INFO',
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
        'file': {
            'level': 'WARNING',
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': LOG_DIR / 'app.log',
            'maxBytes': 10 * 1024 * 1024,
            'backupCount': 5,
            'formatter': 'verbose',
        },
        'trade_file': {
            'level': 'INFO',
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': LOG_DIR / 'trades.log',
            'maxBytes': 10 * 1024 * 1024,
            'backupCount': 10,
            'formatter': 'verbose',
        },
        'error_file': {
            'level': 'ERROR',
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': LOG_DIR / 'errors.log',
            'maxBytes': 10 * 1024 * 1024,
            'backupCount': 10,
            'formatter': 'verbose',
        },
    },
    'loggers': {
        'django': {
            'handlers': ['console', 'file'],
            'level': 'INFO',
            'propagate': True,
        },
        'trading_app': {
            'handlers': ['console', 'trade_file', 'error_file'],
            'level': 'INFO',
            'propagate': False,
        },
        'trading_app.trading_bot': {
            'handlers': ['console', 'trade_file', 'error_file'],
            'level': 'INFO',
            'propagate': False,
        },
        'celery': {
            'handlers': ['console', 'file', 'error_file'],
            'level': 'INFO',
            'propagate': False,
        },
    },
    'root': {
        'handlers': ['console', 'file', 'error_file'],
        'level': 'WARNING',
    },
}
