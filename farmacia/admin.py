from django.contrib import admin
from .models import AuditoriaControlado

@admin.register(AuditoriaControlado)
class AuditoriaControladoAdmin(admin.ModelAdmin):
    list_display = ['timestamp', 'medicamento', 'cantidad_despachada', 'cedula_paciente', 'usuario_despacho', 'ip_origen']
    list_filter = ['medicamento', 'usuario_despacho']
    search_fields = ['cedula_paciente', 'nombre_paciente', 'medicamento__nombre']
    readonly_fields = [
        'medicamento', 'usuario_despacho', 'orden', 'nombre_paciente',
        'cedula_paciente', 'cantidad_despachada', 'stock_antes',
        'stock_despues', 'timestamp', 'ip_origen', 'observacion'
    ]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
