from django.db import models
from django.conf import settings
from administracion.models import Paciente, Medico

class Medicamento(models.Model):
    nombre = models.CharField(max_length=150, verbose_name="Nombre del Medicamento")
    concentracion = models.CharField(max_length=50, help_text="Ej. 500mg, 10ml")
    presentacion = models.CharField(max_length=50, help_text="Ej. Tabletas, Jarabe, Ampollas")
    descripcion = models.TextField(null=True, blank=True, verbose_name="Descripción / Laboratorio", help_text="Información extra del medicamento")
    foto = models.ImageField(upload_to='medicamentos/', null=True, blank=True, verbose_name="Foto del Medicamento")
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
    numero_lote = models.CharField(max_length=50, verbose_name="Número de Lote")
    cantidad_ingresada = models.PositiveIntegerField(verbose_name="Cantidad Inicial")
    cantidad_actual = models.PositiveIntegerField(verbose_name="Stock Disponible en Lote")
    fecha_vencimiento = models.DateField(verbose_name="Fecha de Vencimiento")
    fecha_ingreso = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        # Detectamos si es un lote nuevo que apenas se está registrando
        es_nuevo = self.pk is None 
        super().save(*args, **kwargs)
        
        # Si es nuevo, le sumamos esta cantidad al stock total del Medicamento
        if es_nuevo:
            self.medicamento.stock_actual += self.cantidad_ingresada
            self.medicamento.save()

    def __str__(self):
        return f"Lote {self.numero_lote} ({self.cantidad_actual} un.) - Vence: {self.fecha_vencimiento.strftime('%d/%m/%Y')}"


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
    
