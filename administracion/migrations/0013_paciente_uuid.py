import uuid
from django.db import migrations, models


def generar_uuids(apps, schema_editor):
    """Asigna un UUID único a cada paciente existente."""
    Paciente = apps.get_model('administracion', 'Paciente')
    for paciente in Paciente.objects.all():
        paciente.uuid = uuid.uuid4()
        paciente.save(update_fields=['uuid'])


class Migration(migrations.Migration):

    dependencies = [
        ('administracion', '0012_alter_medico_firma_alter_medico_foto_perfil_and_more'),
    ]

    operations = [
        # Paso 1: Agregar el campo SIN restricción de unicidad todavía
        migrations.AddField(
            model_name='paciente',
            name='uuid',
            field=models.UUIDField(editable=False, null=True),
        ),

        # Paso 2: Llenar cada fila con un UUID único
        migrations.RunPython(generar_uuids, migrations.RunPython.noop),

        # Paso 3: Ahora sí agregar unicidad e índice
        migrations.AlterField(
            model_name='paciente',
            name='uuid',
            field=models.UUIDField(db_index=True, default=uuid.uuid4, editable=False, unique=True, null=False),
        ),
    ]