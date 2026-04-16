from django.contrib import admin
from django.urls import path, include
from core import views as core_views
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('admin_django/', admin.site.urls),
    path('', include('usuarios.urls')),
    path('medico/', include('medico.urls')),
    path('', core_views.home, name='home'),
    path('farmacia/', include('farmacia.urls')),
    path('administracion/', include('administracion.urls')),
    path('laboratorio/', include('laboratorio.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

# Configuración de páginas de error personalizadas
handler404 = 'core.views.error_404'
handler403 = 'core.views.error_403'
handler500 = 'core.views.error_500'