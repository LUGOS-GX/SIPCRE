from django.contrib import admin
from .models import ExpedienteBase, ConsultaEvolucion, Recipe # <-- Y los demás que tengas

admin.site.register(ExpedienteBase)
admin.site.register(ConsultaEvolucion)
admin.site.register(Recipe) 