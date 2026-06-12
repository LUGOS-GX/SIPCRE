# Settings de PRUEBAS: hereda todo de base pero usa SQLite en memoria
# para que los tests corran rápido y sin depender de PostgreSQL.
from .base import *

DEBUG = False
ALLOWED_HOSTS = ['*']

# Si SECRET_KEY no está en el entorno (no hay .env en CI/tests), ponemos una fija.
if not SECRET_KEY:
    SECRET_KEY = 'clave-solo-para-pruebas-no-usar-en-produccion'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    }
}

# django-axes necesita un backend de caché; usamos uno local para tests.
AXES_ENABLED = False  # desactiva el bloqueo por intentos durante los tests

# Password hasher rápido (los tests no necesitan bcrypt real)
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']

# Email en memoria
EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
