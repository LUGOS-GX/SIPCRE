"""
Tests del módulo core: validadores de imagen y normalización de cédulas.
Estos son tests UNITARIOS: prueban funciones puras, sin tocar la base de datos.
"""
import io
from django.test import SimpleTestCase
from django.core.exceptions import ValidationError
from PIL import Image

from core.validators import (
    normalizar_cedula,
    cedula_es_valida,
    validar_imagen,
    CEDULA_MAXIMO,
)


def _imagen_en_memoria(formato='PNG', tamano=(10, 10)):
    """Crea un archivo de imagen válido en memoria para las pruebas."""
    buffer = io.BytesIO()
    Image.new('RGB', tamano, 'white').save(buffer, format=formato)
    buffer.seek(0)
    buffer.size = len(buffer.getvalue())  # validar_imagen lee .size
    return buffer


class NormalizarCedulaTests(SimpleTestCase):
    def test_quita_prefijo_y_puntos(self):
        self.assertEqual(normalizar_cedula('V-12.345.678'), '12345678')

    def test_quita_espacios(self):
        self.assertEqual(normalizar_cedula('  12345678  '), '12345678')

    def test_cadena_sin_digitos_devuelve_vacio(self):
        self.assertEqual(normalizar_cedula('abc'), '')

    def test_none_devuelve_vacio(self):
        self.assertEqual(normalizar_cedula(None), '')

    def test_acepta_numeros(self):
        self.assertEqual(normalizar_cedula(12345678), '12345678')


class CedulaEsValidaTests(SimpleTestCase):
    def test_cedula_normal_es_valida(self):
        self.assertTrue(cedula_es_valida('12345678'))

    def test_cero_no_es_valida(self):
        self.assertFalse(cedula_es_valida('0'))

    def test_vacia_no_es_valida(self):
        self.assertFalse(cedula_es_valida(''))

    def test_sobre_el_tope_no_es_valida(self):
        self.assertFalse(cedula_es_valida(str(CEDULA_MAXIMO + 1)))

    def test_el_tope_exacto_si_es_valido(self):
        self.assertTrue(cedula_es_valida(str(CEDULA_MAXIMO)))


class ValidarImagenTests(SimpleTestCase):
    def test_png_valido_pasa(self):
        # No debe lanzar excepción
        validar_imagen(_imagen_en_memoria('PNG'))

    def test_archivo_de_texto_es_rechazado(self):
        falso = io.BytesIO(b'no soy una imagen')
        falso.size = 17
        with self.assertRaises(ValidationError):
            validar_imagen(falso)

    def test_imagen_muy_grande_es_rechazada(self):
        grande = _imagen_en_memoria('PNG')
        grande.size = 6 * 1024 * 1024  # 6 MB, supera el límite de 5 MB
        with self.assertRaises(ValidationError):
            validar_imagen(grande)
