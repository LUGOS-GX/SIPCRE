from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('administracion', '0013_paciente_uuid'),
    ]

    operations = [
        migrations.AlterField(
            model_name='cita',
            name='estado',
            field=models.CharField(
                choices=[('Pendiente', 'Pendiente'), ('Atendido', 'Atendido')],
                default='Pendiente',
                max_length=20,
            ),
        ),
    ]
