import os
from django.http import FileResponse, Http404
from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.shortcuts import render, redirect
from django.contrib import messages

def home(request):
    return render(request, 'core/home.html')

def error_404(request, exception):
    contexto = {
        'codigo': '404',
        'titulo': 'Página no encontrada',
        'mensaje': 'Lo sentimos, la página que intentas buscar no existe o fue movida.'
    }
    return render(request, 'error.html', contexto, status=404)

def error_403(request, exception):
    contexto = {
        'codigo': '403',
        'titulo': 'Acceso Denegado',
        'mensaje': 'No tienes los permisos necesarios para entrar a este departamento/módulo.'
    }
    return render(request, 'error.html', contexto, status=403)

def error_500(request):
    contexto = {
        'codigo': '500',
        'titulo': 'Error del Servidor',
        'mensaje': '¡Ups! Algo salió mal en nuestro sistema. El equipo técnico ya ha sido notificado.'
    }
    return render(request, 'error.html', contexto, status=500)

@login_required
def serve_media_protegida(request, ruta):
    """
    Sirve archivos de media solo a usuarios autenticados.
    Cualquier intento de acceder a /media/... sin sesión activa
    redirige al login automáticamente.
    """
    media_real = os.path.realpath(settings.MEDIA_ROOT)
    ruta_real = os.path.realpath(os.path.join(settings.MEDIA_ROOT, ruta))

    # 1. Anti path-traversal: la ruta resuelta debe quedar DENTRO de MEDIA_ROOT.
    #    (Se valida ANTES de tocar el disco y con separador para evitar falsos
    #     positivos tipo "/media" vs "/media-secreto".)
    if ruta_real != media_real and not ruta_real.startswith(media_real + os.sep):
        raise Http404

    # 2. Debe existir y ser un archivo (no un directorio).
    if not os.path.isfile(ruta_real):
        raise Http404

    return FileResponse(open(ruta_real, 'rb'))

def lockout_view(request, credentials=None, *args, **kwargs):
    """
    Se muestra cuando un usuario es bloqueado por demasiados intentos fallidos.
    """
    messages.error(
        request,
        'Tu cuenta ha sido bloqueada temporalmente por demasiados intentos fallidos. '
        'Intenta de nuevo en 1 hora.'
    )
    return redirect('login')
