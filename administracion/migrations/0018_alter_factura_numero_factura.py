# numero_factura pasa a null=True para soportar el nuevo esquema de numeración:
# la factura se inserta primero (numero_factura=NULL por un instante) y el
# número se deriva del pk real, eliminando la condición de carrera del
# esquema anterior (last().id + 1). En PostgreSQL, unique=True permite
# múltiples NULL, así que el insert inicial nunca choca.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('administracion', '0017_alter_cita_estado'),
    ]

    operations = [
        migrations.AlterField(
            model_name='factura',
            name='numero_factura',
            field=models.CharField(blank=True, max_length=20, null=True, unique=True),
        ),
    ]
