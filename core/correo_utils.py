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
from django.template.loader import render_to_string
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


# ===========================================================================
#  COMPROBANTE DE PAGO (resumen de compra)
# ===========================================================================
#  Se reutiliza toda la maquinaria de arriba (hilo + adjunto PDF). La única
#  diferencia es que el contexto que recibe la plantilla son SOLO primitivos
#  (strings/Decimals/listas de dicts) ya extraídos de la BD: así el hilo NO
#  toca el ORM y el envío es seguro de disparar después de hacer commit.
# ===========================================================================
from decimal import Decimal

from django.core.validators import validate_email
from django.core.exceptions import ValidationError as _ValidationError

# Métodos de PagoFactura que representan moneda extranjera (el resto es Bs).
_METODOS_USD = {'Efectivo USD', 'Zelle'}


def correo_es_valido(correo):
    """True si 'correo' tiene formato de email válido; False si está vacío o mal."""
    if not correo:
        return False
    try:
        validate_email(correo.strip())
        return True
    except _ValidationError:
        return False


def _fmt_usd(valor):
    """Formato dólar: $1,234.56"""
    return f"${Decimal(valor):,.2f}"


def _fmt_bs(valor):
    """Formato bolívar al estilo venezolano: Bs 1.234,56"""
    s = f"{Decimal(valor):,.2f}"
    # Intercambia separadores (1,234.56 -> 1.234,56) usando un placeholder.
    s = s.replace(',', '\x00').replace('.', ',').replace('\x00', '.')
    return f"Bs {s}"


def enviar_comprobante_pago(*, destinatario, origen, cliente_nombre, cliente_cedula,
                            numero, fecha, lineas, total_usd, pagos):
    """
    Genera el comprobante de pago en PDF y lo envía por correo en segundo plano.

    destinatario   : correo del cliente (debe venir ya validado por el llamador).
    origen         : 'Caja Central', 'Caja de Farmacia', 'Despacho de Farmacia'.
    cliente_nombre : nombre del cliente / paciente.
    cliente_cedula : cédula en formato canónico (solo dígitos).
    numero         : identificador del comprobante (ej. 'FAC-000012', 'Orden #34').
    fecha          : datetime del pago.
    lineas         : lista de dicts {descripcion, cantidad, precio_unitario, subtotal}.
    total_usd      : Decimal con el total de la transacción en USD.
    pagos          : lista de dicts {metodo, moneda ('USD'|'Bs'), monto_original, monto_usd}.
    """
    # Líneas con montos ya formateados (la plantilla solo imprime strings).
    lineas_fmt = [{
        'descripcion': l['descripcion'],
        'cantidad': l['cantidad'],
        'precio_str': _fmt_usd(l['precio_unitario']),
        'subtotal_str': _fmt_usd(l['subtotal']),
    } for l in lineas]

    # Detalle de cada método con su monto en SU moneda (especifica $ o Bs).
    pago_detalle = []
    for p in pagos:
        if p.get('moneda') == 'Bs':
            pago_detalle.append(f"{p['metodo']}: {_fmt_bs(p['monto_original'])}")
        else:
            pago_detalle.append(f"{p['metodo']}: {_fmt_usd(p['monto_usd'])}")

    # Titular "Total pagado": si es un solo método en Bs, se muestra en Bs con su
    # equivalente en $; en cualquier otro caso, el total en $.
    if len(pagos) == 1 and pagos[0].get('moneda') == 'Bs':
        pago_total_str = f"{_fmt_bs(pagos[0]['monto_original'])}  (≈ {_fmt_usd(total_usd)})"
    else:
        pago_total_str = _fmt_usd(total_usd)

    context = {
        'origen': origen,
        'cliente_nombre': cliente_nombre,
        'cliente_cedula': cliente_cedula,
        'numero': numero,
        'fecha_str': fecha.strftime('%d/%m/%Y %I:%M %p'),
        'lineas': lineas_fmt,
        'total_str': _fmt_usd(total_usd),
        'pago_total_str': pago_total_str,
        'pago_detalle': pago_detalle,
    }

    cuerpo = render_to_string('core/correo_comprobante.html', {
        'cliente_nombre': cliente_nombre,
        'origen': origen,
        'numero': numero,
        'pago_total_str': pago_total_str,
    })

    enviar_documento_pdf_async(
        template_pdf='core/comprobante_pago.html',
        context_pdf=context,
        asunto=f"Comprobante de pago {numero} - Cruz Roja Venezolana",
        cuerpo_html=cuerpo,
        destinatario=destinatario,
        nombre_archivo=f"Comprobante_{numero}.pdf".replace(' ', '_').replace('#', ''),
    )
