from django.db import models
from django.conf import settings
from administracion.models import Paciente, Medico
from core.validators import validar_imagen
import os
import uuid
from django.utils import timezone

def renombrar_archivo_seguro(instancia, nombre_archivo):
    ext = nombre_archivo.split('.')[-1].lower()
    nombre_unico = f"{uuid.uuid4().hex}.{ext}"
    nombre_carpeta = instancia.__class__.__name__.lower()
    ruta_final = os.path.join(
        nombre_carpeta,
        timezone.now().strftime('%Y/%m'),
        nombre_unico
    )
    return ruta_final

class Medicamento(models.Model):
    nombre = models.CharField(max_length=150, verbose_name="Nombre del Medicamento")
    concentracion = models.CharField(max_length=50, help_text="Ej. 500mg, 10ml")
    presentacion = models.CharField(max_length=50, help_text="Ej. Tabletas, Jarabe, Ampollas")
    descripcion = models.TextField(null=True, blank=True, verbose_name="Descripción / Laboratorio", help_text="Información extra del medicamento")
    foto = models.ImageField(upload_to=renombrar_archivo_seguro, null=True, blank=True, verbose_name="Foto del Medicamento", validators=[validar_imagen])
    #Control de Inventario
    stock_actual = models.IntegerField(default=0, verbose_name="Stock Disponible")
    stock_minimo = models.IntegerField(default=10, verbose_name="Stock Mínimo de Alerta")
    
    precio = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    fecha_vencimiento = models.DateField(null=True, blank=True)

    # Control de psicotropico
    es_controlado = models.BooleanField(default=False, verbose_name="¿Es Psicotrópico/Controlado?", help_text="Requiere validación estricta de récipe para su despacho.")

    codigo_barras = models.CharField(max_length=50, null=True, blank=True, unique=True, verbose_name="Código de Barras")

    def __str__(self):
        return f"{self.nombre} {self.concentracion} ({self.presentacion}) - Stock: {self.stock_actual}"

    @property
    def stock_critico(self):
        #Devuelve True si el stock actual está por debajo del mínimo
        return self.stock_actual <= self.stock_minimo


class LoteMedicamento(models.Model):
    medicamento = models.ForeignKey(Medicamento, on_delete=models.CASCADE, related_name='lotes')
    # El número de lote ahora lo asigna el sistema automáticamente y de forma
    # secuencial POR medicamento (#001, #002...), para que el farmaceuta no
    # escriba identificadores arbitrarios. blank=True porque ya no viene del
    # formulario; lo rellena generar_numero_lote() dentro de la vista.
    numero_lote = models.CharField(max_length=50, blank=True, verbose_name="Número de Lote")
    cantidad_ingresada = models.PositiveIntegerField(verbose_name="Cantidad Inicial")
    cantidad_actual = models.PositiveIntegerField(verbose_name="Stock Disponible en Lote")
    fecha_vencimiento = models.DateField(verbose_name="Fecha de Vencimiento")
    fecha_ingreso = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Lote {self.numero_lote} ({self.cantidad_actual} un.) - Vence: {self.fecha_vencimiento.strftime('%d/%m/%Y')}"

    @staticmethod
    def generar_numero_lote(medicamento):
        """
        Devuelve el siguiente número correlativo para ESTE medicamento, con el
        formato '#001', '#002'... Cada medicamento lleva su propia secuencia
        (el primer lote de cualquier medicamento es su #001).

        Debe llamarse dentro de una transacción con el medicamento bloqueado
        (select_for_update) para que dos registros simultáneos no calculen el
        mismo número. Se basa en el conteo de lotes ya existentes, así que es
        estable aunque algún número intermedio se hubiera borrado.
        """
        siguiente = medicamento.lotes.count() + 1
        return f"#{siguiente:03d}"


class OrdenFarmacia(models.Model):
    ESTADOS = [
        ('Pendiente', 'Pendiente'),
        ('Despachado', 'Despachado'),
        ('Cancelado', 'Cancelado')
    ]
    
    paciente = models.ForeignKey(Paciente, on_delete=models.CASCADE, related_name='ordenes_farmacia', null=True, blank=True)
    
    nombre_paciente = models.CharField(max_length=150, null=True, blank=True, verbose_name="Nombre (No registrado)")
    cedula_paciente = models.CharField(max_length=20, null=True, blank=True, verbose_name="Cédula (No registrado)")
    
    medico = models.ForeignKey(Medico, on_delete=models.SET_NULL, null=True, blank=True)
    fecha_solicitud = models.DateTimeField(auto_now_add=True)
    fecha_despacho = models.DateTimeField(null=True, blank=True)
    receta_medica_texto = models.TextField(null=True, blank=True, verbose_name="Indicaciones del Médico")
    estado = models.CharField(max_length=20, choices=ESTADOS, default='Pendiente')

    def __str__(self):
        nombre = self.nombre_paciente if self.nombre_paciente else (self.paciente.nombres if self.paciente else "Desconocido")
        return f"Orden #{self.id} - {nombre} ({self.estado})"


class DetalleDespacho(models.Model):
    #Relaciona la orden con los medicamentos físicos que se entregaron
    orden = models.ForeignKey(OrdenFarmacia, on_delete=models.CASCADE, related_name='detalles')
    medicamento = models.ForeignKey(Medicamento, on_delete=models.PROTECT)
    cantidad = models.PositiveIntegerField()
    precio_unitario = models.DecimalField(max_digits=10, decimal_places=2)

    def subtotal(self):
        return self.cantidad * self.precio_unitario

    def __str__(self):
        return f"{self.cantidad}x {self.medicamento.nombre} (Orden #{self.orden.id})"


class MovimientoInventario(models.Model):
    TIPO_CHOICES = [
        ('ENTRADA', 'Entrada (Nuevo Lote)'),
        ('SALIDA', 'Salida (Despacho)'),
        ('DEVOLUCION', 'Devolución al Inventario'),
        ('AJUSTE', 'Ajuste Manual / Merma'),
    ]
    
    medicamento = models.ForeignKey(Medicamento, on_delete=models.CASCADE, related_name='movimientos_kardex')
    tipo_movimiento = models.CharField(max_length=20, choices=TIPO_CHOICES)
    cantidad = models.IntegerField(help_text="Cantidad sumada o restada")
    stock_resultante = models.IntegerField(help_text="Stock físico después de este movimiento")
    fecha = models.DateTimeField(auto_now_add=True)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    referencia = models.CharField(max_length=255, help_text="Ej: Lote #123, Orden #45, Motivo de ajuste")
    
    # Campo opcional para vincularlo directamente a la orden y poder ver los detalles
    orden_relacionada = models.ForeignKey('OrdenFarmacia', on_delete=models.SET_NULL, null=True, blank=True, related_name='movimientos_asociados')

    class Meta:
        ordering = ['-fecha'] # Siempre mostrar los más recientes primero

    def __str__(self):
        return f"{self.get_tipo_movimiento_display()} | {self.medicamento.nombre} | {self.cantidad} uds"
    
class AuditoriaControlado(models.Model):
    """
    Registro inmutable de cada despacho de medicamento psicotrópico/controlado.
    No debe poder editarse ni eliminarse — es una bitácora legal.
    """
    medicamento = models.ForeignKey(Medicamento, on_delete=models.PROTECT, related_name='auditorias')
    usuario_despacho = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    orden = models.ForeignKey(OrdenFarmacia, on_delete=models.PROTECT, related_name='auditorias_controlados', null=True, blank=True)
    
    nombre_paciente = models.CharField(max_length=150)
    cedula_paciente = models.CharField(max_length=20)
    cantidad_despachada = models.PositiveIntegerField()
    stock_antes = models.IntegerField()
    stock_despues = models.IntegerField()
    
    timestamp = models.DateTimeField(auto_now_add=True)  # Inmutable, lo pone Django solo
    ip_origen = models.GenericIPAddressField(null=True, blank=True)
    observacion = models.TextField(blank=True, null=True)

    class Meta:
        ordering = ['-timestamp']
        verbose_name = "Auditoría de Controlado"
        verbose_name_plural = "Auditorías de Controlados"

    def __str__(self):
        return f"{self.timestamp.strftime('%d/%m/%Y %H:%M')} | {self.medicamento.nombre} | {self.cantidad_despachada} uds | {self.cedula_paciente}"
