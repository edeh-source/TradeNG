"""
Django settings for technicians project.
"""

from pathlib import Path
import os
import dj_database_url

from dotenv import load_dotenv
load_dotenv()

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent


# ==================================
# SECURITY
# ==================================

SECRET_KEY = os.environ.get('SECRET_KEY')

DEBUG = os.environ.get('DEBUG', 'False') == 'True'

ALLOWED_HOSTS = ["*"]


# ==================================
# APPLICATIONS
# ==================================

INSTALLED_APPS = [
    "jazzmin",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "users.apps.UsersConfig",
    "core.apps.CoreConfig",
    "jobs.apps.JobsConfig",
    'django_celery_results',
    'django.contrib.humanize',
    'django.contrib.sites',
    'django.contrib.sitemaps',
    'allauth',
    'allauth.account',
    'allauth.socialaccount',
    'allauth.socialaccount.providers.google',
    'allauth.socialaccount.providers.facebook',
]

if not DEBUG:
    INSTALLED_APPS.extend([
        'cloudinary_storage',
        'cloudinary',
    ])

SITE_ID = 1


# ==================================
# MIDDLEWARE
# ==================================

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    'whitenoise.middleware.WhiteNoiseMiddleware',
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    'allauth.account.middleware.AccountMiddleware',
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]


# ==================================
# URLS & WSGI
# ==================================

ROOT_URLCONF = "technicians.urls"

WSGI_APPLICATION = "technicians.wsgi.application"

SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"


# ==================================
# TEMPLATES
# ==================================

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [os.path.join(BASE_DIR, 'templates')],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

if not DEBUG:
    TEMPLATES[0]['APP_DIRS'] = False
    TEMPLATES[0]['OPTIONS']['loaders'] = [
        ('django.template.loaders.cached.Loader', [
            'django.template.loaders.filesystem.Loader',
            'django.template.loaders.app_directories.Loader',
        ]),
    ]


# ==================================
# DATABASE
# ==================================

if DEBUG:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql_psycopg2',
            'NAME': os.environ.get('DB_NAME'),
            'USER': os.environ.get('DB_USER'),
            'PASSWORD': os.environ.get('DB_PASSWORD'),
            'HOST': os.environ.get('DB_HOST'),
            'PORT': os.environ.get('DB_PORT'),
            'CONN_MAX_AGE': 0,
            'OPTIONS': {
                'connect_timeout': 30,
            },
        }
    }
else:
    DATABASE_URL = os.environ.get('DATABASE_URL')
    if not DATABASE_URL:
        raise ValueError("DATABASE_URL environment variable is not set!")

    DATABASES = {
        'default': dj_database_url.config(
            default=DATABASE_URL,
            conn_max_age=600,
            conn_health_checks=True,
            ssl_require=True,
        )
    }


# ==================================
# PASSWORD VALIDATION
# ==================================

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


# ==================================
# INTERNATIONALISATION
# ==================================

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

PHONENUMBER_DEFAULT_REGION = 'NG'


# ==================================
# STATIC & MEDIA FILES
# ==================================

STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'

STATICFILES_FINDERS = [
    'django.contrib.staticfiles.finders.FileSystemFinder',
    'django.contrib.staticfiles.finders.AppDirectoriesFinder',
]

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

if DEBUG:
    STORAGES = {
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
        },
        "staticfiles": {
            "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
        },
    }
else:
    CLOUDINARY_STORAGE = {
        'CLOUD_NAME': os.environ.get('CLOUDINARY_CLOUD_NAME'),
        'API_KEY':    os.environ.get('CLOUDINARY_API_KEY'),
        'API_SECRET': os.environ.get('CLOUDINARY_API_SECRET'),
    }

    if not all([
        CLOUDINARY_STORAGE['CLOUD_NAME'],
        CLOUDINARY_STORAGE['API_KEY'],
        CLOUDINARY_STORAGE['API_SECRET'],
    ]):
        raise ValueError("Cloudinary credentials are not properly set in production!")

    STORAGES = {
        "default": {
            "BACKEND": "cloudinary_storage.storage.MediaCloudinaryStorage",
        },
        "staticfiles": {
            "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
        },
    }

# WhiteNoise settings
WHITENOISE_AUTOREFRESH    = DEBUG
WHITENOISE_USE_FINDERS    = DEBUG
WHITENOISE_MAX_AGE        = 0 if DEBUG else 31536000
WHITENOISE_ALLOW_ALL_ORIGINS = False


# ==================================
# CACHING
# ==================================

if DEBUG:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'unique-snowflake',
            'TIMEOUT': 300,
            'OPTIONS': {
                'MAX_ENTRIES': 1000,
            },
        }
    }
else:
    REDIS_URL = os.environ.get('REDIS_URL')
    if REDIS_URL:
        CACHES = {
            'default': {
                'BACKEND': 'django_redis.cache.RedisCache',
                'LOCATION': REDIS_URL,
                'OPTIONS': {
                    'CLIENT_CLASS': 'django_redis.client.DefaultClient',
                    'CONNECTION_POOL_KWARGS': {
                        'max_connections': 50,
                        'retry_on_timeout': True,
                        'ssl_cert_reqs': None,
                    },
                    'SOCKET_CONNECT_TIMEOUT': 5,
                    'SOCKET_TIMEOUT': 5,
                },
                'KEY_PREFIX': 'globaledge',
                'TIMEOUT': 300,
            }
        }
    else:
        CACHES = {
            'default': {
                'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
                'LOCATION': 'unique-snowflake',
            }
        }


# ==================================
# SESSIONS
# ==================================

SESSION_ENGINE          = 'django.contrib.sessions.backends.cached_db'
SESSION_CACHE_ALIAS     = 'default'
SESSION_COOKIE_AGE      = 1209600   # 2 weeks
SESSION_COOKIE_SECURE   = not DEBUG
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
SESSION_SAVE_EVERY_REQUEST = False


# ==================================
# AUTH
# ==================================

AUTH_USER_MODEL = "users.User"


# ==================================
# CELERY
# ==================================

CELERY_BROKER_URL        = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
CELERY_RESULT_BACKEND    = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
CELERY_ACCEPT_CONTENT    = ['json']
CELERY_TASK_SERIALIZER   = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE          = 'Africa/Lagos'
CELERY_TASK_TRACK_STARTED = True

CELERY_BROKER_TRANSPORT_OPTIONS = {
    'visibility_timeout': 3600,
}
CELERY_REDIS_BACKEND_USE_SSL = {
    'ssl_cert_reqs': None,
}
CELERY_BROKER_USE_SSL = {
    'ssl_cert_reqs': None,
}
# ==================================
# EMAIL
# ==================================

if DEBUG:
    EMAIL_BACKEND       = os.environ.get('EMAIL_BACKEND', 'django.core.mail.backends.smtp.EmailBackend')
    EMAIL_HOST          = os.environ.get('EMAIL_HOST', 'smtp.gmail.com')
    EMAIL_PORT          = int(os.environ.get('EMAIL_PORT', '465'))
    EMAIL_USE_SSL       = os.environ.get('EMAIL_USE_SSL', 'True') == 'True'
    EMAIL_USE_TLS       = os.environ.get('EMAIL_USE_TLS', 'False') == 'True'
    EMAIL_HOST_USER     = os.environ.get('EMAIL_HOST_USER')
    EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD')
    DEFAULT_FROM_EMAIL  = os.environ.get('DEFAULT_FROM_EMAIL', EMAIL_HOST_USER)


# ==================================
# LOGGING
# ==================================
# Streams all errors — including full 500 tracebacks — to stdout so they
# appear in Render's live log feed without needing DEBUG=True.
# django.request at ERROR level is the key logger: it prints the full
# Python traceback for every unhandled exception in a view.
# ==================================

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,

    'formatters': {
        # Used in production — includes timestamp, level, module, and message
        'verbose': {
            'format': (
                '\n[{levelname}] {asctime} | {module} | pid:{process} tid:{thread}\n'
                '{message}\n'
                '─────────────────────────────────────────────────────────\n'
            ),
            'style': '{',
            'datefmt': '%Y-%m-%d %H:%M:%S',
        },
        # Used locally — shorter, easier to scan in the terminal
        'simple': {
            'format': '[{levelname}] {asctime} {module}: {message}',
            'style': '{',
            'datefmt': '%H:%M:%S',
        },
    },

    'handlers': {
        # All output goes to stdout — Render, Railway, Heroku, and every
        # major PaaS captures stdout as the log stream.
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'simple' if DEBUG else 'verbose',
        },
    },

    'root': {
        # Catch-all: anything not handled by a named logger below
        'handlers': ['console'],
        'level': 'WARNING',
    },

    'loggers': {
        # ── Django internals ──────────────────────────────────────────────
        'django': {
            'handlers': ['console'],
            # INFO shows startup messages (migrations run, server start).
            # In production we keep INFO so deploys are easy to follow.
            'level': 'DEBUG' if DEBUG else 'INFO',
            'propagate': False,
        },

        # ── THE MOST IMPORTANT ONE ────────────────────────────────────────
        # django.request logs every HTTP request that results in a 4xx/5xx.
        # At ERROR level it includes the full Python traceback — this is
        # exactly what you need to diagnose your 500 on /profile/worker/edit/
        'django.request': {
            'handlers': ['console'],
            'level': 'ERROR',
            'propagate': False,
        },

        # ── Security warnings (CSRF failures, suspicious requests, etc.) ──
        'django.security': {
            'handlers': ['console'],
            'level': 'ERROR',
            'propagate': False,
        },

        # ── Database queries (very verbose — only enable when hunting a
        #    slow query; leave at WARNING in normal operation) ──────────────
        'django.db.backends': {
            'handlers': ['console'],
            'level': 'WARNING',
            'propagate': False,
        },

        # ── Your app — jobs (AI matching, signals, tasks) ─────────────────
        # DEBUG locally so you see every log.debug() call in your views.
        # INFO in production so important events still appear in Render logs.
        'jobs': {
            'handlers': ['console'],
            'level': 'DEBUG' if DEBUG else 'INFO',
            'propagate': False,
        },

        # ── Your app — users ──────────────────────────────────────────────
        'users': {
            'handlers': ['console'],
            'level': 'DEBUG' if DEBUG else 'INFO',
            'propagate': False,
        },

        # ── Your app — core ───────────────────────────────────────────────
        'core': {
            'handlers': ['console'],
            'level': 'DEBUG' if DEBUG else 'INFO',
            'propagate': False,
        },

        # ── Celery — task execution, retries, failures ────────────────────
        'celery': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
        'celery.task': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
        'celery.worker': {
            'handlers': ['console'],
            'level': 'WARNING',
            'propagate': False,
        },

        # ── django-allauth ────────────────────────────────────────────────
        'allauth': {
            'handlers': ['console'],
            'level': 'WARNING',
            'propagate': False,
        },
    },
}


# ==================================
# MISC
# ==================================

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"