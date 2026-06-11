# numero_lote deja de ser obligatorio en el formulario: ahora lo asigna el
# sistema de forma automática y correlativa por medicamento (#001, #002...).
# El campo sigue existiendo y guardándose; solo cambia que ya no lo escribe
# el usuario, por eso pasa a blank=True.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('farmacia', '0010_normalizar_cedulas_orden'),
    ]

    operations = [
        migrations.AlterField(
            model_name='lotemedicamento',
            name='numero_lote',
            field=models.CharField(blank=True, max_length=50, verbose_name='Número de Lote'),
        ),
    ]
