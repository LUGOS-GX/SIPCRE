"""
Tests del módulo farmacia.

NOTA: este archivo REEMPLAZA por completo el farmacia/tests.py anterior.
Ya incluye los tests de FEFO y stock_critico de la primera sesión, más toda
la cobertura nueva (despacho, caja POS, kardex, lotes, ajustes, buscadores,
estadísticas, BCV y perfil). Si tu copia local tenía solo los de FEFO, este
los contiene; no pierdes nada.

Convenciones:
- SQLite en memoria (settings de prueba), no toca la BD real.
- obtener_tasa_bcv se mockea cuando una vista lo invoca (es una llamada de red).
- descontar_lotes_fefo opera sobre lotes; por eso cada medicamento de prueba
  se crea con un lote, evitando los avisos de "lotes descuadrados".
"""
import json
from datetime import date, time, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from usuarios.models import Usuario
from administracion.models import (
    Paciente, Factura, DetalleFactura, SesionCaja,
)
from farmacia.models import (
    Medicamento, LoteMedicamento, OrdenFarmacia, DetalleDespacho,
    MovimientoInventario, AuditoriaControlado,
)
from farmacia.services import descontar_lotes_fefo, reintegrar_lotes


# ---------- helpers ----------
def _farmaceuta():
    return Usuario.objects.create_user(
        username='farma', email='farma@cruzroja.org', password='clave12345',
        cedula='888', rol='farmacia', telefono='0412',
    )


def _crear_med(nombre='Paracetamol', stock=100, precio='1.50',
               controlado=False, stock_minimo=10, con_lote=True):
    med = Medicamento.objects.create(
        nombre=nombre, concentracion='500mg', presentacion='Tabletas',
        stock_actual=stock, stock_minimo=stock_minimo,
        precio=Decimal(precio), es_controlado=controlado,
    )
    if con_lote and stock > 0:
        LoteMedicamento.objects.create(
            medicamento=med, numero_lote='#001',
            cantidad_ingresada=stock, cantidad_actual=stock,
            fecha_vencimiento=date.today() + timedelta(days=180),
        )
    return med


class BaseFarmaciaTest(TestCase):
    def setUp(self):
        self.farma = _farmaceuta()
        self.client.login(email='farma@cruzroja.org', password='clave12345')


# ============================================================
# 1. Lógica de inventario por lotes (FEFO) — de la 1ª sesión
# ============================================================
class FefoTests(TestCase):
    def setUp(self):
        self.med = Medicamento.objects.create(
            nombre='Paracetamol', concentracion='500mg',
            presentacion='Tabletas', stock_actual=0, precio=1,
        )
        hoy = date.today()
        self.lote_proximo = LoteMedicamento.objects.create(
            medicamento=self.med, numero_lote='#001',
            cantidad_ingresada=10, cantidad_actual=10,
            fecha_vencimiento=hoy + timedelta(days=30),
        )
        self.lote_lejano = LoteMedicamento.objects.create(
            medicamento=self.med, numero_lote='#002',
            cantidad_ingresada=10, cantidad_actual=10,
            fecha_vencimiento=hoy + timedelta(days=365),
        )

    def test_descuenta_primero_el_que_vence_antes(self):
        descontar_lotes_fefo(self.med, 5)
        self.lote_proximo.refresh_from_db()
        self.lote_lejano.refresh_from_db()
        self.assertEqual(self.lote_proximo.cantidad_actual, 5)
        self.assertEqual(self.lote_lejano.cantidad_actual, 10)

    def test_consume_a_traves_de_varios_lotes(self):
        descontar_lotes_fefo(self.med, 15)
        self.lote_proximo.refresh_from_db()
        self.lote_lejano.refresh_from_db()
        self.assertEqual(self.lote_proximo.cantidad_actual, 0)
        self.assertEqual(self.lote_lejano.cantidad_actual, 5)

    def test_reporta_faltante_si_no_alcanza(self):
        self.assertEqual(descontar_lotes_fefo(self.med, 25), 5)

    def test_reintegrar_repone_en_orden(self):
        descontar_lotes_fefo(self.med, 8)
        reintegrar_lotes(self.med, 3)
        self.lote_proximo.refresh_from_db()
        self.assertEqual(self.lote_proximo.cantidad_actual, 5)


class StockCriticoTests(TestCase):
    def test_bajo_minimo_es_critico(self):
        med = _crear_med(stock=5, stock_minimo=10, con_lote=False)
        self.assertTrue(med.stock_critico)

    def test_sobre_minimo_no_es_critico(self):
        med = _crear_med(stock=50, stock_minimo=10, con_lote=False)
        self.assertFalse(med.stock_critico)


# ============================================================
# 2. Despacho de órdenes (y envío de factura a Caja Central)
# ============================================================
class DespachoTests(BaseFarmaciaTest):
    def setUp(self):
        super().setUp()
        self.med = _crear_med('Amoxicilina', stock=50, precio='2.00')
        self.paciente = Paciente.objects.create(
            nombres='Juan Perez', cedula='12345678', tipo_sangre='O+',
        )
        self.orden = OrdenFarmacia.objects.create(
            paciente=self.paciente, estado='Pendiente',
        )
        self.url = reverse('despachar_orden', kwargs={'orden_id': self.orden.id})

    def test_despacho_con_pago_genera_factura_pagada(self):
        self.client.post(self.url, {
            'accion': 'pagar_farmacia',
            'metodo_pago': 'USD',
            'medicamento_id[]': [str(self.med.id)],
            'cantidad[]': ['5'],
        })
        self.orden.refresh_from_db()
        self.med.refresh_from_db()
        self.assertEqual(self.orden.estado, 'Despachado')
        self.assertEqual(self.med.stock_actual, 45)            # 50 - 5
        factura = Factura.objects.latest('id')
        self.assertEqual(factura.estado, 'Pagada')
        self.assertEqual(factura.total, Decimal('10.00'))      # 5 x 2.00

    def test_cuenta_abierta_envia_factura_pendiente_a_caja_central(self):
        self.client.post(self.url, {
            'accion': 'cuenta_abierta',
            'medicamento_id[]': [str(self.med.id)],
            'cantidad[]': ['3'],
        })
        factura = Factura.objects.latest('id')
        self.assertEqual(factura.estado, 'Pendiente')          # queda para Caja Central
        # La cédula se guarda en formato canónico para que Caja Central la cruce
        self.assertEqual(factura.cedula_cliente, '12345678')

    def test_despacho_registra_movimiento_en_kardex(self):
        self.client.post(self.url, {
            'accion': 'pagar_farmacia', 'metodo_pago': 'USD',
            'medicamento_id[]': [str(self.med.id)], 'cantidad[]': ['5'],
        })
        mov = MovimientoInventario.objects.get(orden_relacionada=self.orden)
        self.assertEqual(mov.tipo_movimiento, 'SALIDA')
        self.assertEqual(mov.cantidad, -5)
        self.assertEqual(mov.stock_resultante, 45)

    def test_stock_insuficiente_se_omite(self):
        self.client.post(self.url, {
            'accion': 'pagar_farmacia', 'metodo_pago': 'USD',
            'medicamento_id[]': [str(self.med.id)], 'cantidad[]': ['999'],
        })
        self.orden.refresh_from_db()
        self.med.refresh_from_db()
        # No se despachó nada: stock intacto y orden sigue pendiente
        self.assertEqual(self.med.stock_actual, 50)
        self.assertEqual(self.orden.estado, 'Pendiente')

    def test_controlado_genera_auditoria(self):
        controlado = _crear_med('Clonazepam', stock=20, precio='5.00', controlado=True)
        self.client.post(self.url, {
            'accion': 'pagar_farmacia', 'metodo_pago': 'USD',
            'medicamento_id[]': [str(controlado.id)], 'cantidad[]': ['2'],
        })
        aud = AuditoriaControlado.objects.get(medicamento=controlado)
        self.assertEqual(aud.cantidad_despachada, 2)
        self.assertEqual(aud.stock_antes, 20)
        self.assertEqual(aud.stock_despues, 18)

    def test_no_se_puede_despachar_orden_ya_despachada(self):
        self.orden.estado = 'Despachado'
        self.orden.save()
        resp = self.client.post(self.url, {
            'accion': 'pagar_farmacia', 'medicamento_id[]': [str(self.med.id)],
            'cantidad[]': ['1'],
        })
        self.assertEqual(resp.status_code, 302)   # redirige sin facturar
        self.assertEqual(Factura.objects.count(), 0)

    def test_cancelar_orden_pendiente(self):
        url = reverse('cancelar_orden_farmacia', kwargs={'orden_id': self.orden.id})
        self.client.post(url)
        self.orden.refresh_from_db()
        self.assertEqual(self.orden.estado, 'Cancelado')


# ============================================================
# 3. Caja de Farmacia (venta directa / POS por JSON)
# ============================================================
class CajaFarmaciaTests(BaseFarmaciaTest):
    def setUp(self):
        super().setUp()
        self.med = _crear_med('Ibuprofeno', stock=30, precio='3.00')
        self.url = reverse('caja_farmacia')

    def _vender(self, payload):
        return self.client.post(self.url, data=json.dumps(payload),
                                content_type='application/json')

    def test_venta_directa_exitosa(self):
        resp = self._vender({
            'paciente_nombre': 'Maria Lopez',
            'paciente_cedula': '20111222',
            'metodo_pago': 'USD',
            'carrito': [{'id': self.med.id, 'cantidad': 4}],
        })
        data = resp.json()
        self.assertTrue(data['success'])
        self.med.refresh_from_db()
        self.assertEqual(self.med.stock_actual, 26)            # 30 - 4
        orden = OrdenFarmacia.objects.get(id=data['orden_id'])
        self.assertTrue(DetalleDespacho.objects.filter(orden=orden).exists())
        self.assertTrue(
            MovimientoInventario.objects.filter(
                orden_relacionada=orden, tipo_movimiento='SALIDA').exists()
        )

    def test_venta_sin_comprador_es_rechazada(self):
        resp = self._vender({
            'paciente_nombre': '', 'paciente_cedula': '',
            'carrito': [{'id': self.med.id, 'cantidad': 1}],
        })
        self.assertFalse(resp.json()['success'])
        self.med.refresh_from_db()
        self.assertEqual(self.med.stock_actual, 30)            # nada cambió

    def test_cedula_invalida_es_rechazada(self):
        resp = self._vender({
            'paciente_nombre': 'Cliente Prueba', 'paciente_cedula': '999999999',  # > 40MM
            'carrito': [{'id': self.med.id, 'cantidad': 1}],
        })
        self.assertFalse(resp.json()['success'])

    def test_stock_insuficiente_revierte_todo(self):
        resp = self._vender({
            'paciente_nombre': 'Cliente Prueba', 'paciente_cedula': '20111222',
            'carrito': [{'id': self.med.id, 'cantidad': 999}],
        })
        self.assertFalse(resp.json()['success'])
        self.med.refresh_from_db()
        self.assertEqual(self.med.stock_actual, 30)            # transacción revertida

    def test_controlado_sin_validacion_se_rechaza(self):
        controlado = _crear_med('Morfina', stock=10, precio='9.00', controlado=True)
        resp = self._vender({
            'paciente_nombre': 'Cliente Prueba', 'paciente_cedula': '20111222',
            'validacion_psicotropicos': False,
            'carrito': [{'id': controlado.id, 'cantidad': 1}],
        })
        self.assertFalse(resp.json()['success'])
        controlado.refresh_from_db()
        self.assertEqual(controlado.stock_actual, 10)

    def test_controlado_con_validacion_se_vende_y_audita(self):
        controlado = _crear_med('Morfina', stock=10, precio='9.00', controlado=True)
        resp = self._vender({
            'paciente_nombre': 'Cliente Prueba', 'paciente_cedula': '20111222',
            'validacion_psicotropicos': True,
            'carrito': [{'id': controlado.id, 'cantidad': 1}],
        })
        self.assertTrue(resp.json()['success'])
        self.assertTrue(AuditoriaControlado.objects.filter(medicamento=controlado).exists())


# ============================================================
# 4. Registro de medicamentos (manual) y edición
# ============================================================
class RegistrarMedicamentoTests(BaseFarmaciaTest):
    def test_agregar_medicamento_crea_lote_y_kardex(self):
        self.client.post(reverse('agregar_medicamento'), {
            'nombre': 'Losartán', 'concentracion': '50mg', 'presentacion': 'Tabletas',
            'descripcion': '', 'stock_actual': '40', 'stock_minimo': '10',
            'precio': '1.20', 'fecha_vencimiento': '2027-01-01',
        })
        med = Medicamento.objects.get(nombre='Losartán')
        self.assertEqual(med.stock_actual, 40)
        # Nace con su lote #001 (trazabilidad desde el día 1)
        self.assertEqual(med.lotes.count(), 1)
        self.assertEqual(med.lotes.first().numero_lote, '#001')
        # Y deja constancia de entrada en el kardex
        self.assertTrue(
            MovimientoInventario.objects.filter(
                medicamento=med, tipo_movimiento='ENTRADA').exists()
        )

    def test_editar_medicamento_actualiza_datos(self):
        med = _crear_med('Aspirina', stock=10, precio='1.00')
        self.client.post(reverse('editar_medicamento', kwargs={'med_id': med.id}), {
            'nombre': 'Aspirina', 'concentracion': '100mg', 'presentacion': 'Tabletas',
            'descripcion': '', 'stock_actual': '10', 'stock_minimo': '5',
            'precio': '2.50',
        })
        med.refresh_from_db()
        self.assertEqual(med.precio, Decimal('2.50'))
        self.assertEqual(med.concentracion, '100mg')

    def test_ia_sin_imagen_devuelve_error(self):
        # No probamos Gemini real; solo que la vista rechaza una petición sin imagen.
        resp = self.client.post(reverse('analizar_medicamento_ia'),
                                data=json.dumps({'imagen': ''}),
                                content_type='application/json')
        self.assertFalse(resp.json()['success'])


# ============================================================
# 5. Lotes: registrar, dar de baja, editar fecha
# ============================================================
class LotesTests(BaseFarmaciaTest):
    def setUp(self):
        super().setUp()
        self.med = _crear_med('Vitamina C', stock=20, precio='0.50')

    def test_registrar_lote_suma_stock_y_numera_correlativo(self):
        self.client.post(reverse('registrar_lote'), {
            'medicamento': str(self.med.id),
            'cantidad_ingresada': '30',
            'fecha_vencimiento': '2027-06-01',
        })
        self.med.refresh_from_db()
        self.assertEqual(self.med.stock_actual, 50)            # 20 + 30
        # Ya existía #001 (del helper); el nuevo debe ser #002
        self.assertEqual(self.med.lotes.count(), 2)
        nuevo = self.med.lotes.order_by('-id').first()
        self.assertEqual(nuevo.numero_lote, '#002')
        self.assertEqual(nuevo.cantidad_actual, 30)
        self.assertTrue(
            MovimientoInventario.objects.filter(
                medicamento=self.med, tipo_movimiento='ENTRADA',
                referencia__icontains='#002').exists()
        )

    def test_dar_baja_lote_descuenta_stock_y_registra_merma(self):
        lote = self.med.lotes.first()
        url = reverse('dar_baja_lote', kwargs={'lote_id': lote.id})
        self.client.post(url)
        self.med.refresh_from_db()
        lote.refresh_from_db()
        self.assertEqual(self.med.stock_actual, 0)             # se retiraron las 20
        self.assertEqual(lote.cantidad_actual, 0)
        self.assertTrue(
            MovimientoInventario.objects.filter(
                medicamento=self.med, tipo_movimiento='AJUSTE',
                referencia__icontains='vencimiento').exists()
        )

    def test_editar_fecha_lote_no_mueve_stock(self):
        lote = self.med.lotes.first()
        stock_antes = self.med.stock_actual
        url = reverse('editar_fecha_lote', kwargs={'lote_id': lote.id})
        self.client.post(url, {'fecha_vencimiento': '2028-12-31'})
        lote.refresh_from_db()
        self.med.refresh_from_db()
        self.assertEqual(lote.fecha_vencimiento, date(2028, 12, 31))
        self.assertEqual(self.med.stock_actual, stock_antes)   # solo cambia la fecha


# ============================================================
# 6. Ajustes: devolución y merma (con registro en kardex)
# ============================================================
class AjusteInventarioTests(BaseFarmaciaTest):
    def setUp(self):
        super().setUp()
        self.med = _crear_med('Omeprazol', stock=40, precio='1.00')
        self.url = reverse('ajuste_inventario')

    def test_devolucion_suma_stock_y_registra_kardex(self):
        self.client.post(self.url, {
            'tipo_accion': 'devolucion', 'medicamento': str(self.med.id),
            'cantidad': '5', 'motivo': 'Paciente devolvió',
        })
        self.med.refresh_from_db()
        self.assertEqual(self.med.stock_actual, 45)            # 40 + 5
        self.assertTrue(
            MovimientoInventario.objects.filter(
                medicamento=self.med, tipo_movimiento='DEVOLUCION').exists()
        )

    def test_merma_resta_stock_y_registra_kardex(self):
        self.client.post(self.url, {
            'tipo_accion': 'merma', 'medicamento': str(self.med.id),
            'cantidad': '8', 'motivo': 'Dañado',
        })
        self.med.refresh_from_db()
        self.assertEqual(self.med.stock_actual, 32)            # 40 - 8
        self.assertTrue(
            MovimientoInventario.objects.filter(
                medicamento=self.med, tipo_movimiento='AJUSTE').exists()
        )

    def test_merma_mayor_al_stock_es_rechazada(self):
        self.client.post(self.url, {
            'tipo_accion': 'merma', 'medicamento': str(self.med.id),
            'cantidad': '999', 'motivo': 'Error',
        })
        self.med.refresh_from_db()
        self.assertEqual(self.med.stock_actual, 40)            # sin cambios

    def test_cambio_reintegra_uno_y_descuenta_otro(self):
        med_nuevo = _crear_med('Pantoprazol', stock=20, precio='1.00')
        self.client.post(self.url, {
            'tipo_accion': 'cambio', 'medicamento': str(self.med.id),
            'medicamento_nuevo': str(med_nuevo.id),
            'cantidad': '3', 'motivo': 'Cambio de presentación',
        })
        self.med.refresh_from_db()
        med_nuevo.refresh_from_db()
        self.assertEqual(self.med.stock_actual, 43)            # reintegrado (+3)
        self.assertEqual(med_nuevo.stock_actual, 17)           # entregado (-3)


# ============================================================
# 7. Buscadores en cada zona
# ============================================================
class BuscadoresTests(BaseFarmaciaTest):
    def setUp(self):
        super().setUp()
        self.amox = _crear_med('Amoxicilina', stock=10, precio='1.00')
        self.ibu = _crear_med('Ibuprofeno', stock=10, precio='1.00')

    def test_buscador_inventario_filtra_por_nombre(self):
        resp = self.client.get(reverse('inventario_farmacia'), {'q': 'Amox'})
        ids = [m.id for m in resp.context['page_obj']]
        self.assertIn(self.amox.id, ids)
        self.assertNotIn(self.ibu.id, ids)

    def test_buscador_dashboard_filtra_ordenes(self):
        px = Paciente.objects.create(nombres='Carlos Ruiz', cedula='30111', tipo_sangre='O+')
        orden = OrdenFarmacia.objects.create(paciente=px, estado='Pendiente')
        resp = self.client.get(reverse('dashboard_farmacia'), {'q': 'Carlos'})
        ids = [o.id for o in resp.context['page_obj']]
        self.assertIn(orden.id, ids)

    def test_buscador_kardex_filtra_por_tipo(self):
        MovimientoInventario.objects.create(
            medicamento=self.amox, tipo_movimiento='ENTRADA', cantidad=10,
            stock_resultante=10, referencia='Inicial',
        )
        resp = self.client.get(reverse('kardex_farmacia'), {'q': 'Amoxicilina'})
        meds = [m.medicamento_id for m in resp.context['page_obj']]
        self.assertIn(self.amox.id, meds)

    def test_buscador_gestion_lotes_filtra(self):
        resp = self.client.get(reverse('gestion_lotes'), {'q': 'Amoxicilina'})
        nombres = [l.medicamento.nombre for l in resp.context['page_obj']]
        self.assertTrue(all('Amox' in n for n in nombres))


# ============================================================
# 8. Estadísticas (API + exportación XLSX)
# ============================================================
class EstadisticasTests(BaseFarmaciaTest):
    def setUp(self):
        super().setUp()
        self.med = _crear_med('Amoxicilina', stock=100, precio='1.00')
        orden = OrdenFarmacia.objects.create(nombre_paciente='X', estado='Despachado')
        # Una salida ligada a orden, que es lo que cuentan las estadísticas
        MovimientoInventario.objects.create(
            medicamento=self.med, tipo_movimiento='SALIDA', cantidad=-10,
            stock_resultante=90, referencia='Venta', orden_relacionada=orden,
        )

    def test_api_estadisticas_devuelve_top_meds(self):
        resp = self.client.get(reverse('api_estadisticas_farmacia'))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn('top_meds', data)
        self.assertIn('tendencia', data)
        self.assertEqual(data['top_meds']['data'][0], 10)      # abs(-10)

    def test_exportar_xlsx_descargable(self):
        resp = self.client.get(reverse('exportar_estadisticas_farmacia'))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp['Content-Type'],
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        self.assertIn('attachment', resp['Content-Disposition'])

    def test_requisicion_compra_lista_criticos(self):
        bajo = _crear_med('EnFalta', stock=2, precio='1.00', stock_minimo=10)
        resp = self.client.get(reverse('requisicion_compra'))
        ids = [m.id for m in resp.context['medicamentos']]
        self.assertIn(bajo.id, ids)


# ============================================================
# 9. Tasa BCV: respaldo a la última sesión de caja si la API falla
# ============================================================
class TasaBcvTests(BaseFarmaciaTest):
    @patch('farmacia.views.obtener_tasa_bcv', return_value=Decimal('45.50'))
    def test_caja_usa_tasa_de_la_api(self, _mock):
        resp = self.client.get(reverse('caja_farmacia'))
        self.assertEqual(resp.context['tasa_bcv'], 45.50)

    @patch('farmacia.views.obtener_tasa_bcv', return_value=None)
    def test_caja_cae_a_la_ultima_sesion_si_api_falla(self, _mock):
        SesionCaja.objects.create(cajero=self.farma, tasa_bcv_dia=Decimal('38.00'))
        resp = self.client.get(reverse('caja_farmacia'))
        self.assertEqual(resp.context['tasa_bcv'], 38.00)


# ============================================================
# 10. Perfil de usuario de farmacia
# ============================================================
class PerfilFarmaciaTests(BaseFarmaciaTest):
    def test_ver_perfil(self):
        self.assertEqual(self.client.get(reverse('editar_perfil_farmacia')).status_code, 200)

    def test_modificar_perfil(self):
        self.client.post(reverse('editar_perfil_farmacia'), {
            'nombre_completo': 'Pedro Gomez', 'cedula': '15222333', 'telefono': '04161112233',
        })
        self.farma.refresh_from_db()
        self.assertEqual(self.farma.first_name, 'Pedro')
        self.assertEqual(self.farma.last_name, 'Gomez')
        self.assertEqual(self.farma.cedula, '15222333')
