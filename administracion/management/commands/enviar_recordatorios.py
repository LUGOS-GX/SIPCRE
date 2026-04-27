from django.core.management.base import BaseCommand
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from django.utils import timezone
from administracion.models import Cita
from datetime import timedelta

class Command(BaseCommand):
    help = 'Envía correos electrónicos de recordatorio para las citas del día siguiente.'

    def handle(self, *args, **kwargs):
        # 1. CORRECCIÓN: Forzamos la hora local exacta antes de calcular "mañana"
        hoy_local = timezone.localtime(timezone.now()).date()
        manana = hoy_local + timedelta(days=1)
        
        self.stdout.write(f"--- INICIANDO MOTOR DE RECORDATORIOS ---")
        self.stdout.write(f"Fecha actual del sistema: {hoy_local}")
        self.stdout.write(f"Buscando citas programadas para: {manana}")
        
        # 2. Buscar las citas
        citas_manana = Cita.objects.filter(fecha=manana, estado='Pendiente')
        self.stdout.write(f"Total de citas encontradas para esa fecha: {citas_manana.count()}")
        
        correos_enviados = 0

        for cita in citas_manana:
            # Validamos que el email exista y no sean puros espacios en blanco
            if cita.paciente.email and cita.paciente.email.strip():
                try:
                    asunto = f'Recordatorio de Cita Médica - Cruz Roja'
                    
                    html_content = render_to_string('administracion/correos/recordatorio.html', {'cita': cita})
                    text_content = strip_tags(html_content)

                    msg = EmailMultiAlternatives(
                        asunto, 
                        text_content, 
                        'cruz.roja.bna@gmail.com', 
                        [cita.paciente.email.strip()]
                    )
                    msg.attach_alternative(html_content, "text/html")
                    msg.send()
                    
                    correos_enviados += 1
                    self.stdout.write(self.style.SUCCESS(f'✅ Enviado a: {cita.paciente.nombres} ({cita.paciente.email})'))
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f'❌ Error al enviar a {cita.paciente.email}: {str(e)}'))
            else:
                self.stdout.write(self.style.WARNING(f'⚠️ Omitido: El paciente {cita.paciente.nombres} no tiene correo registrado.'))

        self.stdout.write(self.style.SUCCESS(f'Proceso finalizado. Total enviados: {correos_enviados}'))