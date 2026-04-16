from django.contrib import admin
from .models import Paciente, Cita, Medico

admin.site.register(Paciente)
admin.site.register(Cita)
admin.site.register(Medico)