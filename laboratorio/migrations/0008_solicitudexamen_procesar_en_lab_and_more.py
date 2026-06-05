from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('laboratorio', '0007_alter_solicitudexamen_resultados_archivo'),
    ]

    operations = [
        migrations.AddField(
            model_name='solicitudexamen',
            name='procesar_en_lab',
            field=models.BooleanField(
                default=True,
                verbose_name='Procesar en el laboratorio del ambulatorio',
            ),
        ),
        migrations.AlterField(
            model_name='solicitudexamen',
            name='estado',
            field=models.CharField(
                choices=[
                    ('Pendiente', 'Pendiente'),
                    ('Procesando', 'Procesando'),
                    ('Realizado', 'Realizado'),
                    ('Cancelada', 'Cancelada'),
                    ('Externa', 'Externa'),
                ],
                default='Pendiente',
                max_length=20,
            ),
        ),
    ]
