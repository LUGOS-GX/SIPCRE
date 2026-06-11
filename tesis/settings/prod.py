from .base import *
from django.core.exceptions import ImproperlyConfigured

DEBUG = False

# split(',') sobre un string vacío produce [''] — una lista "con contenido"
# para Django que en realidad no autoriza ningún host y deja el sitio caído
# con un error confuso. Mejor: limpiar vacíos y fallar ruidosamente al
# arrancar si la variable no está configurada.
ALLOWED_HOSTS = [h.strip() for h in os.environ.get('ALLOWED_HOSTS', '').split(',') if h.strip()]
if not ALLOWED_HOSTS:
    raise ImproperlyConfigured(
        "Debe definir la variable de entorno ALLOWED_HOSTS en producción "
        "(ej: ALLOWED_HOSTS=midominio.com,www.midominio.com)."
    )

# Seguridad HTTPS
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_HSTS_PRELOAD = True
SECURE_REFERRER_POLICY = 'same-origin'

# En producción el email sí se envía de verdad
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'