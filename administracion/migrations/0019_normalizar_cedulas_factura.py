# Normaliza las cédulas históricas de Factura.cedula_cliente al formato
# canónico del sistema (solo dígitos). Sin esto, las facturas pendientes
# creadas por Farmacia con formato 'V-12345678' jamás aparecerían al buscar
# deudas desde la Caja Central (que busca con '12345678').
#
# La migración es IDEMPOTENTE: re-ejecutarla sobre datos ya normalizados
# no cambia nada.

from django.db import migrations


def normalizar(valor):
    if not valor:
        return valor
    digitos = ''.join(ch for ch in str(valor) if ch.isdigit())
    return digitos or valor  # si no había ni un dígito, se deja como estaba


def normalizar_cedulas_factura(apps, schema_editor):
    Factura = apps.get_model('administracion', 'Factura')
    for factura in Factura.objects.exclude(cedula_cliente__isnull=True).exclude(cedula_cliente='').iterator():
        limpia = normalizar(factura.cedula_cliente)
        if limpia != factura.cedula_cliente:
            factura.cedula_cliente = limpia
            factura.save(update_fields=['cedula_cliente'])


def noop(apps, schema_editor):
    # No hay forma fiable de restaurar el prefijo original; la normalización
    # no destruye información de identidad (el número es el mismo).
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('administracion', '0018_alter_factura_numero_factura'),
    ]

    operations = [
        migrations.RunPython(normalizar_cedulas_factura, noop),
    ]
