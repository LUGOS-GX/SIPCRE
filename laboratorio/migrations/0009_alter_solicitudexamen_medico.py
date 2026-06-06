from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('laboratorio', '0008_solicitudexamen_procesar_en_lab_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='solicitudexamen',
            name='medico',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='ordenes_lab',
                to='administracion.medico',
            ),
        ),
    ]
