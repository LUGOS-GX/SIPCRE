# Agrega el campo foto_perfil al modelo Usuario.
# El template de editar_perfil de Administración ya mostraba y enviaba este
# campo, pero el modelo no lo tenía: la foto se descartaba silenciosamente.

import core.validators
import usuarios.models
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('usuarios', '0004_remove_usuario_firma_digital'),
    ]

    operations = [
        migrations.AddField(
            model_name='usuario',
            name='foto_perfil',
            field=models.ImageField(
                blank=True,
                null=True,
                upload_to=usuarios.models.ruta_foto_usuario,
                validators=[core.validators.validar_imagen],
            ),
        ),
    ]
