from django.contrib import admin
from .models import SolicitudExamen, ExamenCatalogo, ParametroExamen, ResultadoDetalle

# =======================================================
# INLINES (Para una carga de datos mucho más rápida)
# =======================================================

class ParametroExamenInline(admin.TabularInline):
    model = ParametroExamen
    extra = 1  # Muestra una fila vacía por defecto para agregar parámetros nuevos
    fields = ('nombre', 'unidad_medida', 'rango_minimo', 'rango_maximo', 'valor_referencia_texto')

class ResultadoDetalleInline(admin.TabularInline):
    model = ResultadoDetalle
    extra = 0
    readonly_fields = ('parametro', 'valor_obtenido', 'es_anormal')
    can_delete = False

# =======================================================
# REGISTRO DE MODELOS PRINCIPALES
# =======================================================

@admin.register(ExamenCatalogo)
class ExamenCatalogoAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'activo', 'reactivo_necesario', 'cantidad_reactivo')
    list_filter = ('activo',)
    search_fields = ('nombre', 'descripcion')
    inlines = [ParametroExamenInline] # ¡Aquí inyectamos los parámetros!

@admin.register(SolicitudExamen)
class SolicitudExamenAdmin(admin.ModelAdmin):
    list_display = ('id', 'nombre_paciente', 'cedula_paciente', 'medico', 'fecha_solicitud', 'estado')
    list_filter = ('estado', 'fecha_solicitud')
    search_fields = ('nombre_paciente', 'cedula_paciente', 'id')
    # Opcional: ver los resultados desde el admin de la orden
    inlines = [ResultadoDetalleInline] 

@admin.register(ParametroExamen)
class ParametroExamenAdmin(admin.ModelAdmin):
    list_display = ('examen', 'nombre', 'unidad_medida', 'rango_minimo', 'rango_maximo')
    list_filter = ('examen',)
    search_fields = ('nombre', 'examen__nombre')

@admin.register(ResultadoDetalle)
class ResultadoDetalleAdmin(admin.ModelAdmin):
    list_display = ('orden', 'parametro', 'valor_obtenido', 'es_anormal')
    list_filter = ('es_anormal',)
    search_fields = ('orden__id', 'parametro__nombre', 'valor_obtenido')

