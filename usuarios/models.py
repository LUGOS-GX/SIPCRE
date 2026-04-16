from django.db import models
from django.contrib.auth.models import AbstractUser

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
    
    # --- CAMPOS ESPECÍFICOS PARA MÉDICOS ---
    mpps = models.CharField(max_length=20, blank=True, null=True, verbose_name='MPSS / Matrícula')
    cm = models.CharField(max_length=20, blank=True, null=True, verbose_name='Colegio Médico (CM)')
    especialidad = models.CharField(max_length=50, blank=True, null=True, verbose_name='Especialidad')

    # Configuración para hacer Login con Email
    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['username', 'first_name', 'last_name', 'cedula']

    def __str__(self):
        return f"{self.first_name} {self.last_name} ({self.get_rol_display()})"