# Agrega el campo es_control a Cita. Distingue una cita de control (paciente
# ya registrado, agendada desde administración) de una cita nueva. Es visual:
# cambia el botón del dashboard médico ("Control" vs "Atender") y precarga
# antecedentes. Las citas existentes quedan con es_control=False (comportamiento
# idéntico al actual).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('administracion', '0021_pagofactura_sesion'),
    ]

    operations = [
        migrations.AddField(
            model_name='cita',
            name='es_control',
            field=models.BooleanField(default=False, verbose_name='¿Es cita de control?'),
        ),
    ]
