"""
Generación de PDF a partir de plantillas HTML.

Motor por defecto: Chromium headless vía Playwright. Usa el mismo motor que el
navegador del paciente, así que el PDF que se envía por correo queda visualmente
IDÉNTICO al que el paciente ve e imprime (logos SVG, sello al lado de la firma,
fondos de color, etc.).

El motor está encapsulado detrás de `html_a_pdf()`. Para cambiarlo (por ejemplo a
WeasyPrint si Chromium resulta pesado), basta con definir PDF_ENGINE en settings y
agregar la rama correspondiente; el resto del sistema no cambia.
"""
import logging

from django.conf import settings
from django.template.loader import render_to_string

logger = logging.getLogger('sipcre')


def render_pdf_desde_template(template_name, context):
    """Renderiza una plantilla Django a HTML y devuelve los bytes del PDF."""
    html = render_to_string(template_name, context)
    return html_a_pdf(html)


def html_a_pdf(html):
    """Convierte un string HTML en bytes PDF usando el motor configurado."""
    motor = getattr(settings, 'PDF_ENGINE', 'chromium')
    if motor == 'chromium':
        return _html_a_pdf_chromium(html)
    raise ValueError(f"PDF_ENGINE no soportado: {motor!r}")


def _html_a_pdf_chromium(html):
    """
    Render con Chromium headless (Playwright).

    - print_background=True  -> imprime los fondos de color (barras rojas, recuadros).
    - prefer_css_page_size=True -> respeta el @page de la plantilla (tamaño carta + márgenes).
    - Se lanza y se cierra el navegador por documento para no dejar memoria ocupada
      (mejor para equipos con pocos recursos, a costa de un arranque por PDF).
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(args=['--no-sandbox', '--disable-dev-shm-usage'])
        try:
            page = browser.new_page()
            page.set_content(html, wait_until='load')
            pdf_bytes = page.pdf(print_background=True, prefer_css_page_size=True)
        finally:
            browser.close()
    return pdf_bytes
