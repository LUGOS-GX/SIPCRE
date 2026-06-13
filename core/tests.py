"""
Tests del módulo core.

NOTA: reemplaza por completo el core/tests.py anterior. Incluye los tests de
validadores y cédula de la 1ª sesión, más el filtro de plantilla cedula_puntos,
el validador de imagen/PDF y la protección de archivos media.
"""
import io
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.http import Http404
from django.core.exceptions import ValidationError
from django.test import RequestFactory
from PIL import Image

from core.validators import (
    normalizar_cedula, cedula_es_valida, validar_imagen,
    validar_imagen_o_pdf, CEDULA_MAXIMO,
)
from core.templatetags.formato_ve import cedula_puntos
from core import views as core_views
from usuarios.models import Usuario


def _imagen(formato='PNG'):
    buf = io.BytesIO()
    Image.new('RGB', (10, 10), 'white').save(buf, format=formato)
    buf.seek(0)
    buf.size = len(buf.getvalue())
    return buf


# ---------- Normalización y validación de cédulas ----------
class NormalizarCedulaTests(SimpleTestCase):
    def test_quita_prefijo_y_puntos(self):
        self.assertEqual(normalizar_cedula('V-12.345.678'), '12345678')

    def test_quita_espacios(self):
        self.assertEqual(normalizar_cedula('  12345678  '), '12345678')

    def test_sin_digitos_devuelve_vacio(self):
        self.assertEqual(normalizar_cedula('abc'), '')

    def test_none_devuelve_vacio(self):
        self.assertEqual(normalizar_cedula(None), '')


class CedulaEsValidaTests(SimpleTestCase):
    def test_normal_valida(self):
        self.assertTrue(cedula_es_valida('12345678'))

    def test_cero_invalida(self):
        self.assertFalse(cedula_es_valida('0'))

    def test_vacia_invalida(self):
        self.assertFalse(cedula_es_valida(''))

    def test_sobre_el_tope_invalida(self):
        self.assertFalse(cedula_es_valida(str(CEDULA_MAXIMO + 1)))

    def test_tope_exacto_valido(self):
        self.assertTrue(cedula_es_valida(str(CEDULA_MAXIMO)))


# ---------- Validadores de archivos ----------
class ValidarImagenTests(SimpleTestCase):
    def test_png_valido_pasa(self):
        validar_imagen(_imagen('PNG'))

    def test_texto_rechazado(self):
        falso = io.BytesIO(b'no soy imagen'); falso.size = 13
        with self.assertRaises(ValidationError):
            validar_imagen(falso)

    def test_muy_grande_rechazada(self):
        grande = _imagen('PNG'); grande.size = 6 * 1024 * 1024
        with self.assertRaises(ValidationError):
            validar_imagen(grande)


class ValidarImagenOPdfTests(SimpleTestCase):
    def test_pdf_valido_pasa(self):
        pdf = io.BytesIO(b'%PDF-1.4 contenido'); pdf.size = 18
        validar_imagen_o_pdf(pdf)   # no debe lanzar

    def test_imagen_valida_pasa(self):
        validar_imagen_o_pdf(_imagen('PNG'))

    def test_archivo_raro_rechazado(self):
        falso = io.BytesIO(b'<html></html>'); falso.size = 13
        with self.assertRaises(ValidationError):
            validar_imagen_o_pdf(falso)


# ---------- Filtro de plantilla cedula_puntos ----------
class CedulaPuntosTests(SimpleTestCase):
    def test_agrega_separadores(self):
        self.assertEqual(cedula_puntos('12345678'), '12.345.678')

    def test_tolera_prefijo_viejo(self):
        self.assertEqual(cedula_puntos('V-12345678'), '12.345.678')

    def test_sin_digitos_devuelve_igual(self):
        self.assertEqual(cedula_puntos('---'), '---')


# ---------- Media protegida ----------
class MediaProtegidaTests(TestCase):
    def test_anonimo_es_redirigido_al_login(self):
        resp = self.client.get('/media/algo.png')
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/login', resp.url)

    def test_path_traversal_da_404(self):
        # Aun autenticado, no se puede escapar de MEDIA_ROOT
        user = Usuario.objects.create_user(
            username='u', email='u@x.com', password='clave12345',
            cedula='1', rol='admin', telefono='1')
        request = RequestFactory().get('/media/x')
        request.user = user
        with self.assertRaises(Http404):
            core_views.serve_media_protegida(request, ruta='../../../etc/passwd')
