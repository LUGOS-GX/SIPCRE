# Normaliza las cédulas históricas de OrdenFarmacia.cedula_paciente al formato
# canónico (solo dígitos), por la misma razón que administracion/0019: las
# cédulas con prefijo o puntos rompían el cruce de deudas y la trazabilidad.
# Idempotente.

from django.db import migrations


def normalizar(valor):
    if not valor:
        return valor
    digitos = ''.join(ch for ch in str(valor) if ch.isdigit())
    return digitos or valor


def normalizar_cedulas_orden(apps, schema_editor):
    OrdenFarmacia = apps.get_model('farmacia', 'OrdenFarmacia')
    for orden in OrdenFarmacia.objects.exclude(cedula_paciente__isnull=True).exclude(cedula_paciente='').iterator():
        limpia = normalizar(orden.cedula_paciente)
        if limpia != orden.cedula_paciente:
            orden.cedula_paciente = limpia
            orden.save(update_fields=['cedula_paciente'])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('farmacia', '0009_alter_auditoriacontrolado_orden'),
    ]

    operations = [
        migrations.RunPython(normalizar_cedulas_orden, noop),
    ]
