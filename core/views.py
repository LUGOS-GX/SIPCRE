# Create your views here.
from django.shortcuts import render

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