from django.contrib import admin
from .models import Paciente, Cita, Medico, CatalogoServicio, SesionCaja, PagoFactura

admin.site.register(Paciente)
admin.site.register(Cita)
admin.site.register(Medico)
admin.site.register(CatalogoServicio)
admin.site.register(SesionCaja)
admin.site.register(PagoFactura)
