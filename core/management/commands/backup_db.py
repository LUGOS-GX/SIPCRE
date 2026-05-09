import os
import subprocess
from datetime import datetime
from django.core.management.base import BaseCommand
from django.conf import settings


PG_DUMP = r"C:\Program Files\PostgreSQL\18\bin\pg_dump.exe"


class Command(BaseCommand):
    help = 'Genera un backup de la base de datos PostgreSQL'

    def handle(self, *args, **kwargs):
        # Carpeta de backups
        backup_dir = os.path.join(settings.BASE_DIR, 'backups')
        os.makedirs(backup_dir, exist_ok=True)

        # Nombre del archivo con fecha y hora
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        nombre_archivo = os.path.join(backup_dir, f'sipcre_{timestamp}.sql')

        # Credenciales desde settings
        db = settings.DATABASES['default']

        env = os.environ.copy()
        env['PGPASSWORD'] = db['PASSWORD']

        comando = [
            PG_DUMP,
            '-h', db['HOST'],
            '-p', str(db['PORT']),
            '-U', db['USER'],
            '-d', db['NAME'],
            '-F', 'p',   # formato plain SQL
            '-f', nombre_archivo,
        ]

        try:
            subprocess.run(comando, env=env, check=True)
            self.stdout.write(self.style.SUCCESS(
                f'Backup creado exitosamente: {nombre_archivo}'
            ))
            self._limpiar_backups_viejos(backup_dir)

        except subprocess.CalledProcessError as e:
            self.stdout.write(self.style.ERROR(f'Error al crear backup: {e}'))

    def _limpiar_backups_viejos(self, backup_dir, mantener=7):
        """Elimina backups viejos, conserva solo los últimos N."""
        archivos = sorted([
            os.path.join(backup_dir, f)
            for f in os.listdir(backup_dir)
            if f.startswith('sipcre_') and f.endswith('.sql')
        ])
        por_eliminar = archivos[:-mantener]
        for archivo in por_eliminar:
            os.remove(archivo)
            self.stdout.write(f'Backup antiguo eliminado: {archivo}')