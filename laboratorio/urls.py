from django.urls import path
from . import views

urlpatterns = [
   path('dashboard/', views.dashboard_lab, name='dashboard_lab'),
   path('orden/<int:orden_id>/', views.detalle_orden, name='detalle_orden'),
   path('api/estadisticas/', views.api_estadisticas_laboratorio, name='api_estadisticas_laboratorio'),
   path('exportar-estadisticas/', views.exportar_estadisticas_lab_excel, name='exportar_estadisticas_lab_excel'),
   path('orden/<int:orden_id>/cancelar/', views.cancelar_orden_lab, name='cancelar_orden_lab'),
]