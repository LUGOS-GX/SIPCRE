"""
Tests del módulo administracion: facturación y tasa BCV.
"""
from datetime import time
from decimal import Decimal
from unittest.mock import patch
from django.test import TestCase, SimpleTestCase

from administracion.models import Paciente, Medico, Cita, Factura, DetalleFactura
from administracion.utils import obtener_tasa_bcv


class FacturaTests(TestCase):
    def setUp(self):
        self.paciente = Paciente.objects.create(
            nombres='Juan Perez', cedula='12345678', tipo_sangre='O+',
        )

    def test_numero_factura_se_genera_del_pk(self):
        factura = Factura.objects.create(paciente=self.paciente)
        self.assertEqual(factura.numero_factura, f'FAC-{factura.pk:06d}')

    def test_numeros_de_factura_no_se_repiten(self):
        f1 = Factura.objects.create(paciente=self.paciente)
        f2 = Factura.objects.create(paciente=self.paciente)
        self.assertNotEqual(f1.numero_factura, f2.numero_factura)

    def test_detalle_actualiza_total_de_factura(self):
        factura = Factura.objects.create(paciente=self.paciente)
        DetalleFactura.objects.create(
            factura=factura, departamento='Consulta', descripcion='Cardiología',
            cantidad=2, precio_unitario=Decimal('15.00'),
        )
        factura.refresh_from_db()
        self.assertEqual(factura.total, Decimal('30.00'))


class CitaEstaPagadaTests(TestCase):
    def setUp(self):
        self.paciente = Paciente.objects.create(
            nombres='Ana Gomez', cedula='87654321', tipo_sangre='A+',
        )
        self.medico = Medico.objects.create(nombre='Dr. House', especialidad='General')
        self.cita = Cita.objects.create(
            paciente=self.paciente, medico=self.medico,
            hora=time(10, 0), motivo='Control',
        )

    def test_cita_sin_factura_no_esta_pagada(self):
        self.assertFalse(self.cita.esta_pagada)

    def test_cita_con_factura_pendiente_no_esta_pagada(self):
        Factura.objects.create(paciente=self.paciente, cita=self.cita, estado='Pendiente')
        self.assertFalse(self.cita.esta_pagada)

    def test_cita_con_factura_pagada_si_esta_pagada(self):
        Factura.objects.create(paciente=self.paciente, cita=self.cita, estado='Pagada')
        self.assertTrue(self.cita.esta_pagada)


class TasaBcvTests(SimpleTestCase):
    @patch('administracion.utils.requests.get')
    def test_devuelve_decimal_con_respuesta_valida(self, mock_get):
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {'promedio': 36.5}
        self.assertEqual(obtener_tasa_bcv(), Decimal('36.5'))

    @patch('administracion.utils.requests.get')
    def test_devuelve_none_si_la_api_falla(self, mock_get):
        mock_get.return_value.status_code = 500
        self.assertIsNone(obtener_tasa_bcv())

    @patch('administracion.utils.requests.get')
    def test_devuelve_none_si_tasa_es_cero(self, mock_get):
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {'promedio': 0}
        self.assertIsNone(obtener_tasa_bcv())
