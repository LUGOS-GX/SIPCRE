import os
import uuid
from django.db import models
from django.utils import timezone
from django.conf import settings

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
    telefono = models.CharField(max_length=20, null=True, blank=True)
    direccion = models.TextField(null=True, blank=True)

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
    foto_perfil = models.ImageField(upload_to=renombrar_archivo_seguro, null=True, blank=True)
    firma = models.ImageField(upload_to=renombrar_archivo_seguro, null=True, blank=True)
    sello = models.ImageField(upload_to=renombrar_archivo_seguro, null=True, blank=True)

    def __str__(self):
        return f"{self.nombre} ({self.especialidad})"

class Cita(models.Model):
    ESTADOS = [
        ('Pendiente', 'Pendiente'),
        ('En Sala', 'En Sala'),
        ('Atendido', 'Atendido'),
    ]

    paciente = models.ForeignKey(Paciente, on_delete=models.CASCADE, related_name='citas')
    medico = models.ForeignKey(Medico, on_delete=models.SET_NULL, null=True, related_name='citas')
    
    fecha = models.DateField(default=timezone.now) 
    
    hora = models.TimeField(verbose_name="Hora de la Cita") 
    
    motivo = models.TextField()
    estado = models.CharField(max_length=20, choices=ESTADOS, default='Pendiente')
    
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
    
    # Nuevos campos para pacientes que vienen solo con el récipe
    nombre_cliente = models.CharField(max_length=100, blank=True, null=True, verbose_name="Nombre (Paciente de Paso)")
    cedula_cliente = models.CharField(max_length=20, blank=True, null=True, verbose_name="Cédula (Paciente de Paso)")
    
    numero_factura = models.CharField(max_length=20, unique=True, blank=True)
    fecha_emision = models.DateTimeField(auto_now_add=True)
    fecha_pago = models.DateTimeField(null=True, blank=True)
    estado = models.CharField(max_length=20, choices=ESTADOS_FACTURA, default='Pendiente')
    metodo_pago = models.CharField(max_length=50, blank=True, null=True)
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)

    def save(self, *args, **kwargs):
        if not self.numero_factura:
            ultimo_id = Factura.objects.all().order_by('id').last()
            nuevo_id = 1 if not ultimo_id else ultimo_id.id + 1
            self.numero_factura = f"FAC-{nuevo_id:06d}"
        super().save(*args, **kwargs)

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
