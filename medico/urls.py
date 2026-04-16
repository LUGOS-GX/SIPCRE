from django.urls import path
from . import views

urlpatterns = [
    path('', views.dashboard_medico, name='dashboard_medico'),
    path('atender/<int:cita_id>/', views.atender_paciente, name='atender_paciente'),
    path('historia/nueva/', views.crear_historia_manual, name='crear_historia_manual'),
    path('mis-pacientes/', views.historial_medico, name='historial_medico'),
    path('recipe/nuevo', views.crear_recipe, name='crear_recipe'),
    path('examenes/nuevo', views.solicitar_examenes, name='solicitar_examenes'),
    path('resultados/', views.resultados_examenes, name='resultados_examenes'),
    path('historia/pdf/<int:historia_id>/', views.generar_pdf_historia, name='pdf_historia'),
    path('historia/eliminar/<int:historia_id>/', views.eliminar_historia, name='eliminar_historia'),
    path('control/nuevo/', views.crear_control_rapido, name='crear_control_rapido'),
    path('recipe/pdf/<int:recipe_id>/', views.generar_pdf_recipe, name='pdf_recipe'),
    path('perfil/editar/', views.editar_perfil_medico, name='editar_perfil_medico'),
    path('perfil/firma-sello/', views.cargar_firma_sello, name='cargar_firma_sello'),
    path('orden_pdf/<int:orden_id>/', views.generar_pdf_orden, name='generar_pdf_orden'),
    path('expediente/<int:paciente_id>/', views.ver_expediente_unificado, name='ver_expediente_unificado'),
    path('api/estadisticas/', views.api_estadisticas_medico, name='api_estadisticas_medico'),
    path('paciente/<int:paciente_id>/constancia/', views.generar_constancia, name='generar_constancia'),
    path('exportar-morbilidad/', views.exportar_morbilidad_excel, name='exportar_morbilidad_excel'),
]