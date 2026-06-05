"""
Envío de documentos PDF por correo al paciente, en segundo plano.

Sigue el mismo patrón que el envío de resultados del laboratorio (hilo + adjunto),
pero generalizado para cualquier documento (solicitud, récipe, constancia). El
render del PDF —que es la parte pesada— se hace DENTRO del hilo, de modo que el
médico recibe respuesta inmediata y no se bloquea la petición.
"""
import logging
import threading

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.utils.html import strip_tags

from .pdf_utils import render_pdf_desde_template

logger = logging.getLogger('sipcre')

# Tope de adjunto (15 MB). Estos PDFs son pequeños, pero mantenemos la guarda.
LIMITE_BYTES = 15 * 1024 * 1024


def enviar_documento_pdf_async(*, template_pdf, context_pdf, asunto, cuerpo_html,
                               destinatario, nombre_archivo):
    """
    Lanza en segundo plano la generación del PDF y su envío por correo.

    template_pdf  : ruta de la plantilla a renderizar (la misma que ve el paciente).
    context_pdf   : contexto para esa plantilla (debe incluir firma_b64 / sello_b64).
    asunto        : asunto del correo.
    cuerpo_html   : cuerpo del correo en HTML.
    destinatario  : correo del paciente.
    nombre_archivo: nombre del PDF adjunto.
    """
    hilo = threading.Thread(
        target=_generar_y_enviar,
        kwargs=dict(
            template_pdf=template_pdf,
            context_pdf=context_pdf,
            asunto=asunto,
            cuerpo_html=cuerpo_html,
            destinatario=destinatario,
            nombre_archivo=nombre_archivo,
        ),
        daemon=True,
    )
    hilo.start()


def _generar_y_enviar(*, template_pdf, context_pdf, asunto, cuerpo_html,
                      destinatario, nombre_archivo):
    from django.db import connection
    try:
        pdf_bytes = render_pdf_desde_template(template_pdf, context_pdf)

        if len(pdf_bytes) > LIMITE_BYTES:
            logger.error("PDF '%s' excede %d bytes; no se envía a %s",
                         nombre_archivo, LIMITE_BYTES, destinatario)
            return

        texto_plano = strip_tags(cuerpo_html)
        msg = EmailMultiAlternatives(
            asunto, texto_plano, settings.DEFAULT_FROM_EMAIL, [destinatario]
        )
        msg.attach_alternative(cuerpo_html, "text/html")
        msg.attach(nombre_archivo, pdf_bytes, 'application/pdf')
        msg.send()
        logger.info("Documento '%s' enviado a %s", nombre_archivo, destinatario)
    except Exception as e:
        logger.error("Fallo al enviar '%s' a %s: %s", nombre_archivo, destinatario, e)
    finally:
        # Cerramos la conexión que abrió este hilo para no dejarla colgada.
        connection.close()
