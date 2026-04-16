from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import Usuario

# Configuración básica para ver los campos importantes en la lista
class UsuarioAdmin(UserAdmin):
    model = Usuario
    list_display = ['email', 'first_name', 'last_name', 'cedula', 'rol', 'is_active']
    
    # Esto agrega nuestros campos personalizados al formulario de edición del admin
    fieldsets = UserAdmin.fieldsets + (
        ('Datos Extra', {'fields': ('cedula', 'rol', 'telefono')}),
    )

admin.site.register(Usuario, UsuarioAdmin)