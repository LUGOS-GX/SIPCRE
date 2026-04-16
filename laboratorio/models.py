import os
import uuid
from django.db import models
from django.utils import timezone
from administracion.models import Paciente, Medico
from farmacia.models import Medicamento # Importado para la conexión con el Inventario (Fase 3)

# GENERADOR DE NOMBRES ÚNICOS PARA ARCHIVOS
def renombrar_archivo_seguro(instancia, nombre_archivo):
    """
    Toma el archivo subido, ignora su nombre original por seguridad,
    y le genera un código UUID único, organizándolo por año y mes.
    """
    # 1. Extraemos la extensión original (ej: pdf, jpg)
    ext = nombre_archivo.split('.')[-1].lower()
    
    # 2. Generamos un nombre completamente nuevo y único
    nombre_unico = f"{uuid.uuid4().hex}.{ext}"
    
    # 3. Detectamos el nombre del modelo para la carpeta (solicitudexamen)
    nombre_carpeta = instancia.__class__.__name__.lower()
    
    # 4. Lo organizamos por fecha (Ej: solicitudexamen/2026/03/f3a2...pdf)
    ruta_final = os.path.join(
        nombre_carpeta, 
        timezone.now().strftime('%Y/%m'), 
        nombre_unico
    )
    
    return ruta_final

# =======================================================
# NUEVOS MODELOS: CATÁLOGO Y PARÁMETROS (FASES 1 Y 3)
# =======================================================

class ExamenCatalogo(models.Model):
    nombre = models.CharField(max_length=150, unique=True, verbose_name="Nombre del Examen (Ej: Hematología Completa)")
    descripcion = models.TextField(blank=True, null=True, verbose_name="Descripción / Indicaciones")
    activo = models.BooleanField(default=True, verbose_name="Disponible en Laboratorio")
    
    # FASE 3: Enlace con Farmacia/Inventario para el descuento automático
    reactivo_necesario = models.ForeignKey(Medicamento, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Reactivo a descontar (Farmacia)")
    cantidad_reactivo = models.PositiveIntegerField(default=1, verbose_name="Cantidad a descontar por prueba")

    def __str__(self):
        return self.nombre

class ParametroExamen(models.Model):
    examen = models.ForeignKey(ExamenCatalogo, on_delete=models.CASCADE, related_name='parametros')
    nombre = models.CharField(max_length=100, verbose_name="Parámetro (Ej: Hemoglobina, Glucosa)")
    unidad_medida = models.CharField(max_length=50, blank=True, null=True, verbose_name="Unidad (Ej: g/dL, mg/dL)")
    
    # Valores de Referencia Numéricos (Sirven para pintar de rojo si el paciente está mal)
    rango_minimo = models.FloatField(blank=True, null=True, verbose_name="Valor Mínimo Normal")
    rango_maximo = models.FloatField(blank=True, null=True, verbose_name="Valor Máximo Normal")
    
    # Para parámetros cualitativos (Ej: "Negativo", "No Reactivo")
    valor_referencia_texto = models.CharField(max_length=100, blank=True, null=True, verbose_name="Referencia en Texto")

    def __str__(self):
        return f"{self.examen.nombre} - {self.nombre}"

class SolicitudExamen(models.Model):
    ESTADOS = [
        ('Pendiente', 'Pendiente'),
        ('Procesando', 'Procesando'),
        ('Realizado', 'Realizado'),
        ('Cancelada', 'Cancelada')
    ]

    # px registrado opcional
    paciente = models.ForeignKey(Paciente, on_delete=models.SET_NULL, related_name='ordenes_lab', null=True, blank=True)
    
    nombre_paciente = models.CharField(max_length=150, verbose_name="Nombre del Paciente")
    cedula_paciente = models.CharField(max_length=30, verbose_name="Cédula")
    correo_paciente = models.EmailField(blank=True, null=True, verbose_name="Correo para enviar resultados")
    medico = models.ForeignKey(Medico, on_delete=models.CASCADE, related_name='ordenes_lab')
    
    examenes_solicitados = models.TextField(verbose_name="Exámenes") 
    otros = models.CharField(max_length=200, blank=True, null=True, verbose_name="Otros Exámenes")
    observacion = models.TextField(blank=True, null=True, verbose_name="Observaciones / Nota")
    
    fecha_solicitud = models.DateTimeField(auto_now_add=True)
    estado = models.CharField(max_length=20, choices=ESTADOS, default='Pendiente')
    
    resultados_archivo = models.FileField(upload_to=renombrar_archivo_seguro, blank=True, null=True, verbose_name="Documento de Resultados (Opcional)")
    fecha_resultado = models.DateTimeField(blank=True, null=True)

    def __str__(self):
        return f"Orden #{self.id} - {self.nombre_paciente}"


# =======================================================
# NUEVO MODELO: RESULTADOS ESTRUCTURADOS (FASE 1)
# =======================================================

class ResultadoDetalle(models.Model):
    orden = models.ForeignKey(SolicitudExamen, on_delete=models.CASCADE, related_name='resultados_estructurados')
    parametro = models.ForeignKey(ParametroExamen, on_delete=models.CASCADE)
    
    # Usamos CharField porque un resultado puede ser "15.5" (número) o "Positivo" (texto)
    valor_obtenido = models.CharField(max_length=100, verbose_name="Valor Obtenido")
    
    # Bandera de Inteligencia: El sistema la marcará True si el valor está fuera de rango
    es_anormal = models.BooleanField(default=False, verbose_name="¿Fuera de Rango?")

    def __str__(self):
        return f"Orden #{self.orden.id} | {self.parametro.nombre}: {self.valor_obtenido}"
    
