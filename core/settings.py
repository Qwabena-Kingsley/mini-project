from pathlib import Path
import dj_database_url
import os
from dotenv import load_dotenv
from datetime import timedelta          # ← add this line
# Load .env file
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

# =========================================
# SECURITY
# =========================================
SECRET_KEY = os.environ.get('SECRET_KEY')

DEBUG = os.environ.get('DEBUG', 'True') == 'True'

ALLOWED_HOSTS = os.environ.get('ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',')

# =========================================
# APPLICATIONS
# =========================================
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    # Third party
    'rest_framework',
    'corsheaders',
    # Your apps
    'sensors',
]

# =========================================
# MIDDLEWARE
# =========================================
MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',          # ← must be first
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'core.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'core.wsgi.application'

# =========================================
# DATABASE
# =========================================
import sys

# =========================================
# DATABASE
# =========================================
DATABASE_URL = os.environ.get('DATABASE_URL')

# Print to Railway logs so we can see what is happening
print(f"DEBUG DB: DATABASE_URL found = {bool(DATABASE_URL)}", file=sys.stderr)
print(f"DEBUG DB: DB_NAME = {os.environ.get('DB_NAME', 'NOT SET')}", file=sys.stderr)
print(f"DEBUG DB: DB_HOST = {os.environ.get('DB_HOST', 'NOT SET')}", file=sys.stderr)

if DATABASE_URL:
    DATABASES = {
        'default': dj_database_url.config(
            default=DATABASE_URL,
            conn_max_age=600,
            conn_health_checks=True,
        )
    }
else:
    DATABASES = {
        'default': {
            'ENGINE':   'django.db.backends.postgresql',
            'NAME':     os.environ.get('DB_NAME'),
            'USER':     os.environ.get('DB_USER'),
            'PASSWORD': os.environ.get('DB_PASSWORD'),
            'HOST':     os.environ.get('DB_HOST', 'localhost'),
            'PORT':     os.environ.get('DB_PORT', '5433'),
        }
    }
# =========================================
# DRF
# =========================================
REST_FRAMEWORK = {
    'DEFAULT_RENDERER_CLASSES': [
        'rest_framework.renderers.JSONRenderer',
    ],
    'DEFAULT_PARSER_CLASSES': [
        'rest_framework.parsers.JSONParser',
    ],
}

# =========================================
# CORS
# =========================================
CORS_ALLOWED_ORIGINS = os.environ.get(
    'CORS_ALLOWED_ORIGINS',
    'http://localhost:3000,http://localhost:5173'   # React default ports
).split(',')

# =========================================
# AI / OPENROUTER
# =========================================
OPENROUTER_API_KEY  = os.environ.get('OPENROUTER_API_KEY')
OPENROUTER_BASE_URL = 'https://openrouter.ai/api/v1'
OPENROUTER_MODEL    = 'openai/gpt-4o-mini'



FRONTEND_BASE_URL = os.environ.get('FRONTEND_BASE_URL', 'http://localhost:3000')

# =========================================
# INTERNATIONALIZATION
# =========================================
LANGUAGE_CODE = 'en-us'
TIME_ZONE     = 'UTC'
USE_I18N      = True
USE_TZ        = True

# =========================================
# STATIC FILES
# =========================================
STATIC_URL = 'static/'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


# =========================================
# CELERY — only active if Redis is available
# =========================================
REDIS_URL = os.environ.get('REDIS_URL', None)

if REDIS_URL:
    CELERY_BROKER_URL     = REDIS_URL
    CELERY_RESULT_BACKEND = REDIS_URL
    CELERY_TIMEZONE       = 'UTC'
    CELERY_BEAT_SCHEDULER = 'django_celery_beat.schedulers:DatabaseScheduler'

    CELERY_BEAT_SCHEDULE = {
        'generate-daily-summary': {
            'task':     'sensors.tasks.generate_daily_summary',
            'schedule': timedelta(days=1),
        },
        'check-sensor-health': {
            'task':     'sensors.tasks.check_sensor_health',
            'schedule': timedelta(minutes=30),
        },
    }
# =========================================
# PRODUCTION
# =========================================
import sys

# Gunicorn needs this to serve the app on Railway
ALLOWED_HOSTS = os.environ.get(
    'ALLOWED_HOSTS',
    'localhost,127.0.0.1'
).split(',')

# Static files for production
STATIC_ROOT = BASE_DIR / 'staticfiles'