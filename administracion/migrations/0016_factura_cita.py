from django.db import migrations, models
import django.db.models.deletion
from django.utils import timezone


def backfill_cita(apps, schema_editor):
    """ Enlaza las facturas YA pagadas con su cita usando el criterio histórico
        (mismo paciente + misma fecha). Se ejecuta una sola vez. """
    Factura = apps.get_model('administracion', 'Factura')
    Cita = apps.get_model('administracion', 'Cita')

    for factura in Factura.objects.filter(estado='Pagada', cita__isnull=True).iterator():
        if not factura.fecha_emision:
            continue
        fecha = timezone.localtime(factura.fecha_emision).date()

        cita = None
        if factura.paciente_id:
            cita = (Cita.objects
                    .filter(paciente_id=factura.paciente_id, fecha=fecha)
                    .order_by('id').first())
        if cita is None and factura.cedula_cliente:
            cita = (Cita.objects
                    .filter(paciente__cedula=factura.cedula_cliente, fecha=fecha)
                    .order_by('id').first())

        if cita:
            Factura.objects.filter(pk=factura.pk).update(cita=cita)


class Migration(migrations.Migration):

    dependencies = [
        ('administracion', '0015_alter_cita_estado'),
    ]

    operations = [
        migrations.AddField(
            model_name='factura',
            name='cita',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='facturas',
                to='administracion.cita',
            ),
        ),
        migrations.RunPython(backfill_cita, migrations.RunPython.noop),
    ]
