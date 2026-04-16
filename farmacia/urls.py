from django.urls import path
from . import views

urlpatterns = [
    path('', views.dashboard_farmacia, name='dashboard_farmacia'),
    path('despachar/<int:orden_id>/', views.despachar_orden, name='despachar_orden'),
    path('perfil/', views.editar_perfil_farmacia, name='editar_perfil_farmacia'),
    path('inventario/', views.inventario_farmacia, name='inventario_farmacia'),
    path('inventario/agregar/', views.agregar_medicamento, name='agregar_medicamento'),
    path('inventario/editar/<int:med_id>/', views.editar_medicamento, name='editar_medicamento'),
    path('inventario/eliminar/<int:med_id>/', views.eliminar_medicamento, name='eliminar_medicamento'),
    path('inventario/lote/nuevo/', views.registrar_lote, name='registrar_lote'),
    path('api/estadisticas/', views.api_estadisticas_farmacia, name='api_estadisticas_farmacia'),
    path('api/estadisticas/exportar/', views.exportar_estadisticas_farmacia, name='exportar_estadisticas_farmacia'),
    path('kardex/', views.kardex_farmacia, name='kardex_farmacia'),
    path('ajuste/', views.ajuste_inventario, name='ajuste_inventario'),
    path('lotes/', views.gestion_lotes, name='gestion_lotes'),
    path('requisicion-compra/', views.requisicion_compra, name='requisicion_compra'),
    path('caja/', views.caja_farmacia, name='caja_farmacia'),
    path('api/analizar-medicamento/', views.analizar_imagen_medicamento, name='analizar_medicamento_ia'),
]
