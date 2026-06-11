# Normaliza las cédulas históricas de Paciente.cedula al formato canónico
# (solo dígitos). Los pacientes creados antes de los parches (ej. vía historia
# manual) quedaron con formatos como '1.234.567' o 'V-1234567', lo que produce
# el listado con cédulas mezcladas y rompe los cruces por cédula.
#
# IMPORTANTE: cedula es unique=True. Si al limpiar '12.345.678' resulta que ya
# existe otro paciente '12345678', NO se puede renombrar sin fusionar
# expedientes (decisión clínica, no automática): ese caso se deja intacto y se
# reporta por consola para revisión manual. El filtro de template cedula_puntos
# los muestra uniformes de todas formas.
#
# Idempotente: re-ejecutarla sobre datos limpios no cambia nada.

from django.db import migrations


def normalizar(valor):
    if not valor:
        return valor
    digitos = ''.join(ch for ch in str(valor) if ch.isdigit())
    return digitos or valor


def normalizar_cedulas_paciente(apps, schema_editor):
    Paciente = apps.get_model('administracion', 'Paciente')
    colisiones = []

    for paciente in Paciente.objects.exclude(cedula__isnull=True).exclude(cedula='').iterator():
        limpia = normalizar(paciente.cedula)
        if limpia == paciente.cedula:
            continue
        if Paciente.objects.filter(cedula=limpia).exclude(pk=paciente.pk).exists():
            colisiones.append((paciente.pk, paciente.cedula, limpia))
            continue
        paciente.cedula = limpia
        paciente.save(update_fields=['cedula'])

    if colisiones:
        print("\n  [AVISO] Pacientes duplicados detectados (misma cédula con distinto formato).")
        print("  Revisar y fusionar manualmente — la migración los dejó intactos:")
        for pk, original, limpia in colisiones:
            print(f"    - Paciente id={pk}: '{original}' colisiona con un paciente existente '{limpia}'")


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('administracion', '0019_normalizar_cedulas_factura'),
    ]

    operations = [
        migrations.RunPython(normalizar_cedulas_paciente, noop),
    ]
