from django.db import models
from django.utils import timezone
from administracion.models import Paciente, Medico, Cita
import os
import uuid

#Para que no se repitan nombres de arhivos
def renombrar_archivo_seguro(instancia, nombre_archivo):
    """
    Toma el archivo subido, ignora su nombre original por seguridad,
    y le genera un código UUID único en el mundo, organizándolo por año y mes.
    """
    # 1. Extraemos la extensión original (ej: jpg, png, pdf)
    ext = nombre_archivo.split('.')[-1].lower()
    
    # 2. Generamos un nombre completamente nuevo y único
    nombre_unico = f"{uuid.uuid4().hex}.{ext}"
    
    # 3. Detectamos de qué modelo viene para crearle su propia carpeta
    nombre_carpeta = instancia.__class__.__name__.lower()
    
    # 4. Lo organizamos por fecha (Ej: consultaevolucion/2026/03/a1b2c3...jpg)
    ruta_final = os.path.join(
        nombre_carpeta, 
        timezone.now().strftime('%Y/%m'), 
        nombre_unico
    )
    
    return ruta_final

# ---1. BASE ----
class ExpedienteBase(models.Model):
    paciente = models.OneToOneField('administracion.Paciente', on_delete=models.CASCADE, related_name='expediente_medico')
    tipo_sangre = models.CharField(max_length=5, null=True, blank=True, verbose_name="Tipo de Sangre")
    alergias = models.TextField(null=True, blank=True, verbose_name="Alergias Conocidas")
    
    # Mudamos los antecedentes permanentes aquí
    antecedentes_personales = models.TextField(null=True, blank=True, verbose_name="Antecedentes Personales")
    antecedentes_familiares = models.TextField(null=True, blank=True, verbose_name="Antecedentes Familiares")
    
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Expediente Base - {self.paciente.nombres}"

# --- 2. CONSULTA / EVOLUCIÓN (Dinámico, una por cada visita) ---
class ConsultaEvolucion(models.Model):
    # Conexiones actualizadas
    expediente = models.ForeignKey(ExpedienteBase, on_delete=models.CASCADE, related_name='consultas')
    medico = models.ForeignKey('administracion.Medico', on_delete=models.SET_NULL, null=True)
    cita = models.OneToOneField('administracion.Cita', on_delete=models.SET_NULL, null=True, blank=True)
    
    fecha = models.DateField(default=timezone.now)
    hora = models.TimeField(auto_now_add=True)

    # --- 1. SIGNOS VITALES ---
    tension_arterial = models.CharField(max_length=20, null=True, blank=True, verbose_name="Tensión Arterial")
    frecuencia_cardiaca = models.CharField(max_length=10, null=True, blank=True, verbose_name="FC")
    frecuencia_respiratoria = models.CharField(max_length=10, null=True, blank=True, verbose_name="FR")
    temperatura = models.CharField(max_length=10, null=True, blank=True, verbose_name="Temp")
    peso = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True, verbose_name="Peso")
    talla = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True, verbose_name="Talla")
    saturacion_oxigeno = models.CharField(max_length=10, null=True, blank=True, verbose_name="SatO2 (%)")
    glicemia = models.CharField(max_length=10, null=True, blank=True, verbose_name="GLIC (mg/dl)")

    # --- 2. ANAMNESIS DE LA VISITA ---
    motivo_consulta = models.TextField(null=True, blank=True, verbose_name="Motivo de Consulta")
    enfermedad_actual = models.TextField(null=True, blank=True, verbose_name="Enfermedad Actual")
    
    # --- 3. EXAMEN FÍSICO ---
    examen_fisico = models.TextField(verbose_name="Examen Físico Completo", blank=True, null=True)
    
    # --- 4. CONCLUSIONES ---
    diagnostico = models.TextField(null=True, blank=True, verbose_name="Diagnóstico")
    plan_tratamiento = models.TextField(null=True, blank=True, verbose_name="Plan / Tratamiento")

    #------ 5. ANEXOS --------
    TIPO_ANEXO = [('Lab', 'Laboratorio'), ('Img', 'Imagenología'), ('Otro', 'Otro')]
    tipo_anexo = models.CharField(max_length=10, choices=TIPO_ANEXO, null=True, blank=True)
    
    # ¡AQUÍ ESTÁ LA MAGIA APLICADA!
    archivo_anexo = models.ImageField(upload_to=renombrar_archivo_seguro, null=True, blank=True)
    
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fecha', '-hora']

    def __str__(self):
        return f"Consulta: {self.expediente.paciente} ({self.fecha})"

#RECIPE
class Recipe(models.Model):
    medico = models.ForeignKey('administracion.Medico', on_delete=models.CASCADE)
    fecha = models.DateTimeField(auto_now_add=True) 
    medicamentos = models.TextField()
    indicaciones = models.TextField()
    nombre_paciente = models.CharField(max_length=150, null=True, blank=True)
    cedula_paciente = models.CharField(max_length=20, null=True, blank=True)

    def __str__(self):
        return f"Récipe #{self.id} - Dr(a). {self.medico.nombre} ({self.fecha.strftime('%d/%m/%Y')})"

#CONSTANCIA MEDICA
class ConstanciaMedica(models.Model):
    paciente = models.ForeignKey('administracion.Paciente', on_delete=models.CASCADE)
    medico = models.ForeignKey('administracion.Medico', on_delete=models.CASCADE)
    fecha_emision = models.DateTimeField(auto_now_add=True)
    motivo_texto = models.TextField(verbose_name="Texto de la Constancia")
    codigo_verificacion = models.CharField(max_length=15, unique=True, blank=True)

    def save(self, *args, **kwargs):
        if not self.codigo_verificacion:
            # Genera un código único alfanumérico de 10 caracteres (Ej: 8F3A2B9C1D)
            self.codigo_verificacion = str(uuid.uuid4().hex)[:10].upper()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Constancia {self.codigo_verificacion} - {self.paciente}"
