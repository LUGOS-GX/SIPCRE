from django.urls import path
from . import views

urlpatterns = [
    path('', views.dashboard_admin, name='dashboard_admin'),
    path('cita/nueva', views.agendar_cita, name='agendar_cita'),
    path('orden/externa', views.registrar_orden_externa, name='registrar_orden_externa'),
    path('cita/editar/<int:id_cita>/', views.editar_cita, name='editar_cita'),
    path('caja/', views.caja_central, name='caja_central'),
    path('sala-espera/', views.sala_espera, name='sala_espera'),
    path('aprobar-usuario/<int:usuario_id>/', views.aprobar_usuario, name='aprobar_usuario'),
    path('rechazar-usuario/<int:usuario_id>/', views.rechazar_usuario, name='rechazar_usuario'),
]