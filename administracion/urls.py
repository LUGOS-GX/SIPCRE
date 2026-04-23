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
    path('historial/', views.historial_citas, name='historial_citas'),
    path('cita/<int:cita_id>/eliminar/', views.eliminar_cita, name='eliminar_cita'),
    path('personal/', views.lista_personal, name='lista_personal'),
    path('caja/deudas/<str:cedula>/', views.obtener_deudas_paciente, name='obtener_deudas_paciente'),
    path('caja/cerrar/', views.cerrar_caja, name='cerrar_caja'),
    path('caja/reporte/<int:sesion_id>/', views.imprimir_cierre, name='imprimir_cierre'),
    path('caja/historico/', views.historico_caja, name='historico_caja'),
    path('estadisticas/datos/', views.datos_estadisticas, name='datos_estadisticas'),
    path('estadisticas/excel/<str:tipo>/', views.exportar_excel_estadisticas, name='exportar_excel_estadisticas'),
    path('estadisticas/pdf/', views.pdf_estadisticas, name='pdf_estadisticas'),
]