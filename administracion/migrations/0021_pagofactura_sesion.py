# Agrega el FK sesion a PagoFactura: el arqueo de cierre pasa a sumar solo los
# pagos de la sesión del cajero que cierra, en vez de todos los pagos del
# sistema desde la fecha de apertura. Los pagos históricos quedan con NULL
# (sus arqueos ya están congelados en los totales de las sesiones cerradas).

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('administracion', '0020_normalizar_cedulas_paciente'),
    ]

    operations = [
        migrations.AddField(
            model_name='pagofactura',
            name='sesion',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='pagos',
                to='administracion.sesioncaja',
            ),
        ),
    ]
