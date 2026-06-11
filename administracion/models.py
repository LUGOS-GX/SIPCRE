import os
import uuid
from django.db import models
from django.utils import timezone
from django.conf import settings
from decimal import Decimal
from core.validators import validar_imagen
import uuid

# GENERADOR DE NOMBRES ÚNICOS PARA ARCHIVOS
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
    
    # 4. Lo organizamos por fecha (Ej: medico/2026/03/a1b2c3...png)
    ruta_final = os.path.join(
        nombre_carpeta, 
        timezone.now().strftime('%Y/%m'), 
        nombre_unico
    )
    
    return ruta_final

class Paciente(models.Model):
    NACIONALIDAD_CHOICES = [('V', 'Venezolano'), ('E', 'Extranjero')]
    SANGRE_CHOICES = [
        ('O+', 'O Positivo'), ('O-', 'O Negativo'),
        ('A+', 'A Positivo'), ('A-', 'A Negativo'),
        ('B+', 'B Positivo'), ('B-', 'B Negativo'),
        ('AB+', 'AB Positivo'), ('AB-', 'AB Negativo'),
    ]
    nombres = models.CharField(max_length=150, verbose_name="Nombre Completo")
    nacionalidad = models.CharField(max_length=1, choices=NACIONALIDAD_CHOICES, default='V')
    cedula = models.CharField(max_length=15, unique=True, verbose_name="Cédula") 
    tipo_sangre = models.CharField(max_length=3, choices=SANGRE_CHOICES)
    fecha_nacimiento = models.DateField(null=True, blank=True)
    tiene_seguro = models.BooleanField(default=False)
    nombre_seguro = models.CharField(max_length=100, blank=True, null=True)
    creado_en = models.DateTimeField(auto_now_add=True)
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True, db_index=True)
    telefono = models.CharField(max_length=20, null=True, blank=True)
    direccion = models.TextField(null=True, blank=True)
    email = models.EmailField(max_length=150, blank=True, null=True)

    def __str__(self):
        return f"{self.nacionalidad}-{self.cedula} | {self.nombres}"

class Medico(models.Model):
    usuario = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True)
    nombre = models.CharField(max_length=100) 
    especialidad = models.CharField(max_length=50) 
    cupo_diario = models.IntegerField(default=10) 
    telefono = models.CharField(max_length=20, blank=True, null=True)
    email = models.EmailField(blank=True, null=True)
    cm = models.CharField(max_length=10, null=True, blank=True, verbose_name="Colegio Médico")
    
    # ¡AQUÍ ESTÁ LA MAGIA APLICADA A LAS IMÁGENES DEL MÉDICO!
    foto_perfil = models.ImageField(upload_to=renombrar_archivo_seguro, null=True, blank=True, validators=[validar_imagen])
    firma = models.ImageField(upload_to=renombrar_archivo_seguro, null=True, blank=True, validators=[validar_imagen])
    sello = models.ImageField(upload_to=renombrar_archivo_seguro, null=True, blank=True, validators=[validar_imagen])

    def __str__(self):
        return f"{self.nombre} ({self.especialidad})"

class Cita(models.Model):
    ESTADOS = [
        ('Pendiente', 'Pendiente'),
        ('Atendido', 'Atendido'),
    ]

    paciente = models.ForeignKey(Paciente, on_delete=models.CASCADE, related_name='citas')
    medico = models.ForeignKey(Medico, on_delete=models.SET_NULL, null=True, related_name='citas')
    
    fecha = models.DateField(default=timezone.now) 
    
    hora = models.TimeField(verbose_name="Hora de la Cita") 
    
    motivo = models.TextField()
    estado = models.CharField(max_length=20, choices=ESTADOS, default='Pendiente')

    @property
    def esta_pagada(self):
        """ La cita está paga si tiene al menos una factura enlazada en estado 'Pagada'.
            Enlace directo (FK), sin depender de la fecha. """
        return self.facturas.filter(estado='Pagada').exists()
    
    def __str__(self):
        return f"{self.fecha} - {self.paciente} con {self.medico}"

class Factura(models.Model):
    ESTADOS_FACTURA = (
        ('Pendiente', 'Pendiente de Pago'),
        ('Pagada', 'Pagada'),
        ('Anulada', 'Anulada'),
    )
    
    # Permitimos que paciente sea nulo (para los pacientes de paso)
    paciente = models.ForeignKey('Paciente', on_delete=models.PROTECT, related_name='facturas', null=True, blank=True)
    
    nombre_cliente = models.CharField(max_length=100, blank=True, null=True, verbose_name="Nombre (Paciente de Paso)")
    cedula_cliente = models.CharField(max_length=20, blank=True, null=True, verbose_name="Cédula (Paciente de Paso)")
    cita = models.ForeignKey('Cita', on_delete=models.SET_NULL, related_name='facturas', null=True, blank=True)
    numero_factura = models.CharField(max_length=20, unique=True, null=True, blank=True)
    fecha_emision = models.DateTimeField(auto_now_add=True)
    fecha_pago = models.DateTimeField(null=True, blank=True)
    estado = models.CharField(max_length=20, choices=ESTADOS_FACTURA, default='Pendiente')
    metodo_pago = models.CharField(max_length=50, blank=True, null=True)
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)

    def save(self, *args, **kwargs):
        """
        El número de factura se deriva del pk REAL después del INSERT.
        El esquema anterior (leer el último id y sumarle 1) tenía una condición
        de carrera: dos facturas simultáneas podían calcular el mismo número y
        chocar contra el unique=True. El pk lo asigna la secuencia de PostgreSQL,
        que es atómica, así que nunca se repite. En la práctica produce los
        mismos números FAC-000001, FAC-000002... que el esquema viejo.
        """
        es_nueva_sin_numero = self._state.adding and not self.numero_factura
        super().save(*args, **kwargs)
        if es_nueva_sin_numero:
            self.numero_factura = f"FAC-{self.pk:06d}"
            super().save(update_fields=['numero_factura'])

    def __str__(self):
        nombre = self.paciente.nombres if self.paciente else self.nombre_cliente
        return f"{self.numero_factura} - {nombre} (${self.total})"

class DetalleFactura(models.Model):
    factura = models.ForeignKey(Factura, on_delete=models.CASCADE, related_name='detalles')
    departamento = models.CharField(max_length=50) # 'Consulta', 'Laboratorio', 'Farmacia'
    descripcion = models.CharField(max_length=255) # Ej: "Consulta Cardiología", "Perfil 20", "Losartán 50mg"
    cantidad = models.PositiveIntegerField(default=1)
    precio_unitario = models.DecimalField(max_digits=10, decimal_places=2)
    subtotal = models.DecimalField(max_digits=10, decimal_places=2)

    def save(self, *args, **kwargs):
        self.subtotal = self.cantidad * self.precio_unitario
        super().save(*args, **kwargs)
        
        # Actualizamos el total de la factura padre automáticamente
        total_factura = sum(detalle.subtotal for detalle in self.factura.detalles.all())
        self.factura.total = total_factura
        self.factura.save()

    def __str__(self):
        return f"{self.cantidad}x {self.descripcion} ({self.departamento})"

class SesionCaja(models.Model):
    """ Registro de apertura y cierre de caja por día/cajero para el reporte PDF """
    cajero = models.ForeignKey('usuarios.Usuario', on_delete=models.PROTECT)
    fecha_apertura = models.DateTimeField(auto_now_add=True)
    fecha_cierre = models.DateTimeField(null=True, blank=True)
    tasa_bcv_dia = models.DecimalField(max_digits=10, decimal_places=2, help_text="Tasa fijada al abrir la caja")
    estado = models.CharField(max_length=20, default='Abierta', choices=[('Abierta', 'Abierta'), ('Cerrada', 'Cerrada')])
    
    # Totales calculados al momento del cierre (Auditoría)
    total_usd_efectivo = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    total_zelle = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    total_bs_efectivo = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    total_pago_movil = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    total_punto_venta = models.DecimalField(max_digits=15, decimal_places=2, default=0)

    def __str__(self):
        return f"Caja {self.id} - {self.cajero.username} - {self.estado}"

class CatalogoServicio(models.Model):
    """ Para poblar el panel derecho del POS con servicios rápidos """
    CATEGORIAS = [
        ('Consulta', 'Consulta Médica'),
        ('Laboratorio', 'Laboratorio'),
        ('Imagenologia', 'Rayos X / Eco'),
        ('Enfermeria', 'Servicios de Enfermería'),
        ('Otro', 'Otro')
    ]
    nombre = models.CharField(max_length=150)
    categoria = models.CharField(max_length=50, choices=CATEGORIAS)
    precio_usd = models.DecimalField(max_digits=10, decimal_places=2)
    activo = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.nombre} (${self.precio_usd})"

class PagoFactura(models.Model):
    """ Permite pagos mixtos (Ej: $20 en efectivo y el resto en Pago Móvil) """
    METODOS = [
        ('Efectivo USD', 'Efectivo USD'),
        ('Zelle', 'Zelle'),
        ('Efectivo Bs', 'Efectivo Bs'),
        ('Pago Movil', 'Pago Móvil'),
        ('Punto de Venta', 'Punto de Venta')
    ]
    factura = models.ForeignKey(Factura, related_name='pagos', on_delete=models.CASCADE)
    metodo = models.CharField(max_length=50, choices=METODOS)
    monto_moneda_original = models.DecimalField(max_digits=15, decimal_places=2, help_text="Lo que entregó el px (Bs o USD)")
    monto_equivalente_usd = models.DecimalField(max_digits=15, decimal_places=2, help_text="El valor que representa en la factura")
    referencia = models.CharField(max_length=100, blank=True, null=True, help_text="Nro de recibo o Zelle")
    fecha_pago = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return f"Pago {self.metodo} - Factura {self.factura.id}"
    