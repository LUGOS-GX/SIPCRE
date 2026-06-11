"""
WSGI config for tesis project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/wsgi/
"""

import os

from django.core.wsgi import get_wsgi_application

# Este archivo lo carga el servidor de aplicación (gunicorn/uwsgi) en el
# despliegue real: su default debe ser PRODUCCIÓN. Con el default anterior
# ('dev'), bastaba olvidar la variable de entorno para desplegar con
# DEBUG=True y trazas completas expuestas al público.
# Para desarrollo local se usa manage.py, que sí apunta a 'dev'.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'tesis.settings.prod')

application = get_wsgi_application()
