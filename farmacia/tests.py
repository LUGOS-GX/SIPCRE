"""
Tests del módulo farmacia: lógica de inventario por lotes (FEFO).
Estos son tests de INTEGRACIÓN ligera: usan la base de datos de prueba
(SQLite en memoria) para crear medicamentos y lotes reales.
"""
from datetime import date, timedelta
from django.test import TestCase
from django.db import transaction

from farmacia.models import Medicamento, LoteMedicamento
from farmacia.services import descontar_lotes_fefo, reintegrar_lotes


class FefoTests(TestCase):
    def setUp(self):
        self.med = Medicamento.objects.create(
            nombre='Paracetamol', concentracion='500mg',
            presentacion='Tabletas', stock_actual=0, precio=1,
        )
        hoy = date.today()
        # Lote que vence ANTES (debe consumirse primero por FEFO)
        self.lote_proximo = LoteMedicamento.objects.create(
            medicamento=self.med, numero_lote='#001',
            cantidad_ingresada=10, cantidad_actual=10,
            fecha_vencimiento=hoy + timedelta(days=30),
        )
        # Lote que vence DESPUÉS
        self.lote_lejano = LoteMedicamento.objects.create(
            medicamento=self.med, numero_lote='#002',
            cantidad_ingresada=10, cantidad_actual=10,
            fecha_vencimiento=hoy + timedelta(days=365),
        )

    def test_descuenta_primero_el_lote_que_vence_antes(self):
        with transaction.atomic():
            faltante = descontar_lotes_fefo(self.med, 5)
        self.lote_proximo.refresh_from_db()
        self.lote_lejano.refresh_from_db()
        self.assertEqual(faltante, 0)
        self.assertEqual(self.lote_proximo.cantidad_actual, 5)   # consumió de aquí
        self.assertEqual(self.lote_lejano.cantidad_actual, 10)   # intacto

    def test_consume_a_traves_de_varios_lotes(self):
        with transaction.atomic():
            faltante = descontar_lotes_fefo(self.med, 15)
        self.lote_proximo.refresh_from_db()
        self.lote_lejano.refresh_from_db()
        self.assertEqual(faltante, 0)
        self.assertEqual(self.lote_proximo.cantidad_actual, 0)   # vaciado
        self.assertEqual(self.lote_lejano.cantidad_actual, 5)    # 10 - 5

    def test_reporta_faltante_si_los_lotes_no_alcanzan(self):
        with transaction.atomic():
            faltante = descontar_lotes_fefo(self.med, 25)  # solo hay 20
        self.assertEqual(faltante, 5)

    def test_cantidad_cero_no_hace_nada(self):
        with transaction.atomic():
            faltante = descontar_lotes_fefo(self.med, 0)
        self.assertEqual(faltante, 0)

    def test_reintegrar_repone_en_orden_fefo(self):
        with transaction.atomic():
            descontar_lotes_fefo(self.med, 8)   # deja lote_proximo en 2
        with transaction.atomic():
            sobrante = reintegrar_lotes(self.med, 3)
        self.lote_proximo.refresh_from_db()
        self.assertEqual(sobrante, 0)
        self.assertEqual(self.lote_proximo.cantidad_actual, 5)  # 2 + 3 repuestos


class StockCriticoTests(TestCase):
    def test_stock_bajo_minimo_es_critico(self):
        med = Medicamento.objects.create(
            nombre='Ibuprofeno', concentracion='400mg', presentacion='Tabletas',
            stock_actual=5, stock_minimo=10, precio=1,
        )
        self.assertTrue(med.stock_critico)

    def test_stock_sobre_minimo_no_es_critico(self):
        med = Medicamento.objects.create(
            nombre='Ibuprofeno', concentracion='400mg', presentacion='Tabletas',
            stock_actual=50, stock_minimo=10, precio=1,
        )
        self.assertFalse(med.stock_critico)
