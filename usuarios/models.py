import os
import uuid
from django.db import models
from django.utils import timezone
from django.contrib.auth.models import AbstractUser
from core.validators import validar_imagen


def ruta_foto_usuario(instancia, nombre_archivo):
    """
    Ignora el nombre original del archivo por seguridad y genera un nombre
    UUID único, organizado por año/mes (mismo patrón que el resto del sistema).
    Ej: usuario/2026/06/a1b2c3...png
    """
    ext = nombre_archivo.split('.')[-1].lower()
    nombre_unico = f"{uuid.uuid4().hex}.{ext}"
    return os.path.join('usuario', timezone.now().strftime('%Y/%m'), nombre_unico)


class Usuario(AbstractUser):
    # Roles disponibles
    ROLES = (
        ('admin', 'Administración'),
        ('medico', 'Médico'),
        ('farmacia', 'Farmacia'),
        ('laboratorio', 'Laboratorio e Imágenes'),
    )

    email = models.EmailField(unique=True, verbose_name='Correo Electrónico')
    cedula = models.CharField(max_length=15, unique=True, verbose_name='Cédula')
    rol = models.CharField(max_length=20, choices=ROLES, verbose_name='Departamento')
    telefono = models.CharField(max_length=20, verbose_name='Teléfono')

    # Foto de perfil del usuario (la usa el perfil de Administración; los
    # médicos siguen usando Medico.foto_perfil para su ficha clínica).
    foto_perfil = models.ImageField(upload_to=ruta_foto_usuario, null=True, blank=True, validators=[validar_imagen])

    # --- CAMPOS ESPECÍFICOS PARA MÉDICOS ---
    mpps = models.CharField(max_length=20, blank=True, null=True, verbose_name='MPSS / Matrícula')
    cm = models.CharField(max_length=20, blank=True, null=True, verbose_name='Colegio Médico (CM)')
    especialidad = models.CharField(max_length=50, blank=True, null=True, verbose_name='Especialidad')

    # Configuración para hacer Login con Email
    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['username', 'first_name', 'last_name', 'cedula']

    def __str__(self):
        return f"{self.first_name} {self.last_name} ({self.get_rol_display()})"
