from pathlib import Path
import os
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent.parent

SECRET_KEY = os.environ.get('SECRET_KEY')

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'axes',
    'core',
    'medico',
    'farmacia',
    'laboratorio',
    'administracion',
    'usuarios',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'axes.middleware.AxesMiddleware',
]

ROOT_URLCONF = 'tesis.urls'

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

WSGI_APPLICATION = 'tesis.wsgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.environ.get('DB_NAME'),
        'USER': os.environ.get('DB_USER'),
        'PASSWORD': os.environ.get('DB_PASSWORD'),
        'HOST': os.environ.get('DB_HOST', 'localhost'),
        'PORT': os.environ.get('DB_PORT', '5432'),
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

AUTHENTICATION_BACKENDS = [
    'axes.backends.AxesStandaloneBackend', 
    'django.contrib.auth.backends.ModelBackend',
]

LANGUAGE_CODE = 'es-ve'
TIME_ZONE = 'America/Caracas'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATICFILES_DIRS = [
    os.path.join(BASE_DIR, 'static')
]

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')

AUTH_USER_MODEL = 'usuarios.Usuario'

#CONFIGURACIÓN DE EMAIL
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = 'smtp.gmail.com'
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD')
DEFAULT_FROM_EMAIL = os.environ.get('EMAIL_HOST_USER')

# Configuración de protección contra fuerza bruta
AXES_FAILURE_LIMIT = 5           # Bloquear después de 5 intentos fallidos
AXES_COOLOFF_TIME = 1            # Bloqueo dura 1 hora
AXES_LOCKOUT_CALLABLE = 'core.views.lockout_view'  # Vista personalizada al bloquearse
AXES_RESET_ON_SUCCESS = True     # Si logra entrar, resetea el contador de fallos
AXES_LOCKOUT_PARAMETERS = ['username', 'ip_address']  # Bloquea por usuario + IP

# --- HEADERS DE SEGURIDAD ---
# necesario para los PDFs en iframes
X_FRAME_OPTIONS = 'SAMEORIGIN'

# Evita que el navegador adivine el tipo de contenido de un archivo
# Previene ataques donde se sube un archivo disfrazado (ej: imagen que es un script)
SECURE_CONTENT_TYPE_NOSNIFF = True

# Activa el filtro XSS del navegador (Cross-Site Scripting)
SECURE_BROWSER_XSS_FILTER = True

# Las cookies de sesión no se pueden leer desde JavaScript
# Previene robo de sesión si hay XSS
SESSION_COOKIE_HTTPONLY = True

# La cookie CSRF tampoco es accesible desde JavaScript
CSRF_COOKIE_HTTPONLY = True

# Tiempo de sesión: 8 horas de inactividad cierra la sesión automáticamente
# Importante para terminales compartidas en clínicas
SESSION_COOKIE_AGE = 28800
SESSION_EXPIRE_AT_BROWSER_CLOSE = True

# --- LOGGING ---
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,

    'formatters': {
        'detallado': {
            'format': '[{asctime}] {levelname} {name} | {message}',
            'style': '{',
            'datefmt': '%d/%m/%Y %H:%M:%S',
        },
    },

    'handlers': {
        # Errores graves → archivo separado
        'archivo_errores': {
            'level': 'ERROR',
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': BASE_DIR / 'logs' / 'errores.log',
            'maxBytes': 1024 * 1024 * 5,  # 5 MB por archivo
            'backupCount': 5,              # Guarda los últimos 5 archivos
            'formatter': 'detallado',
            'encoding': 'utf-8',
        },
        # Actividad general → archivo separado
        'archivo_actividad': {
            'level': 'INFO',
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': BASE_DIR / 'logs' / 'actividad.log',
            'maxBytes': 1024 * 1024 * 5,
            'backupCount': 5,
            'formatter': 'detallado',
            'encoding': 'utf-8',
        },
        # Consola (para desarrollo)
        'consola': {
            'class': 'logging.StreamHandler',
            'formatter': 'detallado',
        },
    },

    'loggers': {
        # Errores de Django
        'django': {
            'handlers': ['archivo_errores', 'consola'],
            'level': 'ERROR',
            'propagate': False,
        },
        # Actividad del sistema SIPCRE
        'sipcre': {
            'handlers': ['archivo_actividad', 'consola'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}
