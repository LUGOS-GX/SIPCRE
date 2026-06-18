"""
Tests del módulo administracion.

NOTA: este archivo REEMPLAZA por completo el administracion/tests.py anterior.
Incluye los tests de la 1ª sesión (numeración de factura, esta_pagada, tasa BCV)
y agrega toda la cobertura nueva: buscadores, servicios (agendar cita, orden
externa, editar cita), caja central (cobro, cuentas abiertas, recibir cobros,
cierre, histórico, BCV), estadísticas y exportación, historial de atendidos,
gestión de personal (aprobar/rechazar), validaciones y perfil.

Convenciones:
- SQLite en memoria (settings de prueba); no toca la BD real.
- obtener_tasa_bcv se mockea cuando una vista lo invoca (es llamada de red).
- La caja central crea una sesión al entrar; en los tests de cobro se
  pre-crea una SesionCaja abierta para no golpear la API del BCV.
"""
import json
from datetime import time, date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, SimpleTestCase
from django.urls import reverse
from django.core import mail
from django.utils import timezone

from usuarios.models import Usuario
from administracion.models import (
    Paciente, Medico, Cita, Factura, DetalleFactura,
    SesionCaja, CatalogoServicio, PagoFactura,
)
from administracion.utils import obtener_tasa_bcv


def _admin():
    return Usuario.objects.create_user(
        username='admin1', email='admin@cruzroja.org', password='clave12345',
        cedula='777', rol='admin', telefono='0412',
    )


class BaseAdminTest(TestCase):
    def setUp(self):
        self.admin = _admin()
        self.client.login(email='admin@cruzroja.org', password='clave12345')
        self.medico = Medico.objects.create(nombre='Dr. House', especialidad='General')


# ============================================================
# 1. Facturación y Cita.esta_pagada (de la 1ª sesión)
# ============================================================
class FacturaTests(TestCase):
    def setUp(self):
        self.paciente = Paciente.objects.create(
            nombres='Juan Perez', cedula='12345678', tipo_sangre='O+')

    def test_numero_factura_se_genera_del_pk(self):
        f = Factura.objects.create(paciente=self.paciente)
        self.assertEqual(f.numero_factura, f'FAC-{f.pk:06d}')

    def test_numeros_no_se_repiten(self):
        f1 = Factura.objects.create(paciente=self.paciente)
        f2 = Factura.objects.create(paciente=self.paciente)
        self.assertNotEqual(f1.numero_factura, f2.numero_factura)

    def test_detalle_actualiza_total(self):
        f = Factura.objects.create(paciente=self.paciente)
        DetalleFactura.objects.create(
            factura=f, departamento='Consulta', descripcion='Cardiología',
            cantidad=2, precio_unitario=Decimal('15.00'))
        f.refresh_from_db()
        self.assertEqual(f.total, Decimal('30.00'))


class CitaEstaPagadaTests(TestCase):
    def setUp(self):
        self.px = Paciente.objects.create(nombres='Ana', cedula='87654321', tipo_sangre='A+')
        self.med = Medico.objects.create(nombre='Dr. X', especialidad='General')
        self.cita = Cita.objects.create(paciente=self.px, medico=self.med,
                                         hora=time(10, 0), motivo='Control')

    def test_sin_factura_no_pagada(self):
        self.assertFalse(self.cita.esta_pagada)

    def test_factura_pendiente_no_pagada(self):
        Factura.objects.create(paciente=self.px, cita=self.cita, estado='Pendiente')
        self.assertFalse(self.cita.esta_pagada)

    def test_factura_pagada_si_pagada(self):
        Factura.objects.create(paciente=self.px, cita=self.cita, estado='Pagada')
        self.assertTrue(self.cita.esta_pagada)


class TasaBcvUtilTests(SimpleTestCase):
    @patch('administracion.utils.requests.get')
    def test_decimal_con_respuesta_valida(self, mock_get):
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {'promedio': 36.5}
        self.assertEqual(obtener_tasa_bcv(), Decimal('36.5'))

    @patch('administracion.utils.requests.get')
    def test_none_si_api_falla(self, mock_get):
        mock_get.return_value.status_code = 500
        self.assertIsNone(obtener_tasa_bcv())

    @patch('administracion.utils.requests.get')
    def test_none_si_tasa_cero(self, mock_get):
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {'promedio': 0}
        self.assertIsNone(obtener_tasa_bcv())


# ============================================================
# 2. Agendar cita (pagar ahora / cuenta abierta) + validaciones
# ============================================================
class AgendarCitaTests(BaseAdminTest):
    def setUp(self):
        super().setUp()
        self.servicio = CatalogoServicio.objects.create(
            nombre='Consulta General', categoria='Consulta',
            precio_usd=Decimal('20.00'), activo=True)
        self.url = reverse('agendar_cita')

    def _datos_base(self, **extra):
        datos = {
            'cedula': '13000111', 'nombre_nuevo': 'Pedro Nuevo',
            'medico_id': str(self.medico.id), 'fecha': '2026-07-01',
            'hora': '09:00', 'motivo': 'Chequeo',
        }
        datos.update(extra)
        return datos

    def test_cita_cuenta_abierta_genera_factura_pendiente(self):
        self.client.post(self.url, self._datos_base(**{'servicios[]': [str(self.servicio.id)]}))
        cita = Cita.objects.get(paciente__cedula='13000111')
        self.assertEqual(cita.estado, 'Pendiente')
        factura = Factura.objects.get(cita=cita)
        self.assertEqual(factura.estado, 'Pendiente')          # deuda abierta
        self.assertEqual(factura.total, Decimal('20.00'))

    def test_cita_pagar_ahora_redirige_a_caja_con_carrito(self):
        resp = self.client.post(self.url, self._datos_base(
            pagar_ahora='1', **{'servicios[]': [str(self.servicio.id)]}))
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse('caja_central'), resp.url)
        self.assertIn('carrito_express', self.client.session)

    def test_cedula_invalida_no_agenda(self):
        self.client.post(self.url, self._datos_base(cedula='999999999'))  # > 40MM
        self.assertFalse(Paciente.objects.filter(nombres='Pedro Nuevo').exists())

    def test_falta_medico_no_agenda(self):
        self.client.post(self.url, self._datos_base(medico_id=''))
        self.assertFalse(Cita.objects.exists())

    def test_cedula_duplicada_sin_control_se_bloquea(self):
        Paciente.objects.create(nombres='Existente', cedula='13000111', tipo_sangre='O+')
        self.client.post(self.url, self._datos_base())   # sin es_control
        # No se crea una segunda cita para esa cédula
        self.assertEqual(Cita.objects.filter(paciente__cedula='13000111').count(), 0)

    def test_control_sin_paciente_existente_se_bloquea(self):
        self.client.post(self.url, self._datos_base(es_control='1'))
        self.assertFalse(Cita.objects.exists())


# ============================================================
# 3. Orden externa (laboratorio) + validaciones
# ============================================================
class OrdenExternaTests(BaseAdminTest):
    def setUp(self):
        super().setUp()
        self.url = reverse('registrar_orden_externa')

    def test_orden_externa_crea_paciente_y_solicitud(self):
        from laboratorio.models import SolicitudExamen
        resp = self.client.post(self.url, {
            'nombre': 'Laura Diaz', 'nacionalidad': 'V', 'cedula': '14555666',
            'examenes': ['Hematología', 'Glicemia'], 'correo_paciente': 'laura@x.com',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse('caja_central'), resp.url)
        self.assertTrue(Paciente.objects.filter(cedula='14555666').exists())
        sol = SolicitudExamen.objects.get(cedula_paciente='V-14555666')
        self.assertEqual(sol.estado, 'Pendiente')
        self.assertTrue(sol.procesar_en_lab)
        self.assertIn('Hematología', sol.examenes_solicitados)

    def test_sin_examenes_no_crea_orden(self):
        from laboratorio.models import SolicitudExamen
        self.client.post(self.url, {
            'nombre': 'Laura', 'cedula': '14555666', 'examenes': [],
        })
        self.assertFalse(SolicitudExamen.objects.exists())

    def test_cedula_invalida_no_crea_orden(self):
        from laboratorio.models import SolicitudExamen
        self.client.post(self.url, {
            'nombre': 'Laura', 'cedula': '999999999', 'examenes': ['Glicemia'],
        })
        self.assertFalse(SolicitudExamen.objects.exists())


# ============================================================
# 4. Editar cita
# ============================================================
class EditarCitaTests(BaseAdminTest):
    def setUp(self):
        super().setUp()
        self.px = Paciente.objects.create(nombres='Original', cedula='15000222', tipo_sangre='O+')
        self.cita = Cita.objects.create(paciente=self.px, medico=self.medico,
                                        fecha='2026-07-01', hora=time(9, 0), motivo='X')
        self.url = reverse('editar_cita', kwargs={'id_cita': self.cita.id})

    def test_editar_actualiza_paciente_y_cita(self):
        self.client.post(self.url, {
            'cedula': '15000222', 'nombre_nuevo': 'Nombre Cambiado',
            'medico_id': str(self.medico.id), 'fecha': '2026-08-15',
            'hora': '11:30', 'motivo': 'Actualizado', 'tipo_sangre': 'A+',
        })
        self.px.refresh_from_db()
        self.cita.refresh_from_db()
        self.assertEqual(self.px.nombres, 'Nombre Cambiado')
        self.assertEqual(str(self.cita.fecha), '2026-08-15')
        self.assertEqual(self.cita.motivo, 'Actualizado')

    def test_editar_con_cedula_invalida_no_guarda(self):
        self.client.post(self.url, {
            'cedula': '999999999', 'nombre_nuevo': 'No Debe Cambiar',
            'medico_id': str(self.medico.id), 'fecha': '2026-08-15', 'hora': '11:30',
        })
        self.px.refresh_from_db()
        self.assertEqual(self.px.nombres, 'Original')


# ============================================================
# 5. Buscadores y filtros por fecha
# ============================================================
class BuscadoresYFiltrosTests(BaseAdminTest):
    def test_buscador_personal_filtra(self):
        Usuario.objects.create_user(username='m1', email='m1@x.com', password='x',
                                    cedula='1', rol='medico', telefono='1',
                                    first_name='Gregorio', last_name='Lima')
        resp = self.client.get(reverse('lista_personal'), {'q': 'Gregorio'})
        encontrados = [u.first_name for u in resp.context['personal']['medicos']]
        self.assertIn('Gregorio', encontrados)

    def test_api_buscar_pacientes(self):
        Paciente.objects.create(nombres='Roberto Solo', cedula='16000333', tipo_sangre='O+')
        resp = self.client.get(reverse('api_buscar_pacientes'), {'q': 'Roberto'})
        nombres = [p['nombres'] for p in resp.json()['resultados']]
        self.assertIn('Roberto Solo', nombres)

    def test_api_buscar_pacientes_query_corta_devuelve_vacio(self):
        resp = self.client.get(reverse('api_buscar_pacientes'), {'q': 'a'})
        self.assertEqual(resp.json()['resultados'], [])

    def test_dashboard_filtra_por_fecha(self):
        px = Paciente.objects.create(nombres='Px Fecha', cedula='17000444', tipo_sangre='O+')
        Cita.objects.create(paciente=px, medico=self.medico, fecha='2026-07-10',
                            hora=time(9, 0), motivo='X', estado='Pendiente')
        resp = self.client.get(reverse('dashboard_admin'), {'fecha': '2026-07-10'})
        ids = [c.id for c in resp.context['citas']]
        self.assertEqual(len(ids), 1)
        resp2 = self.client.get(reverse('dashboard_admin'), {'fecha': '2026-07-11'})
        self.assertEqual(len(list(resp2.context['citas'])), 0)

    def test_historico_caja_filtra_por_busqueda(self):
        Factura.objects.create(nombre_cliente='Cliente Buscado', cedula_cliente='18000555',
                               estado='Pagada')
        resp = self.client.get(reverse('historico_caja'), {'q': 'Cliente Buscado'})
        nombres = [f.nombre_cliente for f in resp.context['facturas']]
        self.assertIn('Cliente Buscado', nombres)


# ============================================================
# 6. Caja Central: cobro, recibir deudas, validaciones, cierre
# ============================================================
class CajaCentralTests(BaseAdminTest):
    def setUp(self):
        super().setUp()
        # Sesión abierta pre-creada: así caja_central no llama a la API del BCV.
        self.sesion = SesionCaja.objects.create(
            cajero=self.admin, tasa_bcv_dia=Decimal('40.00'), estado='Abierta')
        self.servicio = CatalogoServicio.objects.create(
            nombre='Consulta', categoria='Consulta', precio_usd=Decimal('25.00'), activo=True)
        self.url = reverse('caja_central')

    def _cobrar(self, payload):
        return self.client.post(self.url, data=json.dumps(payload),
                                content_type='application/json',
                                HTTP_X_REQUESTED_WITH='XMLHttpRequest')

    def test_cobro_servicio_nuevo_genera_factura_pagada(self):
        resp = self._cobrar({
            'cedula': '19000666', 'nombre': 'Cliente Caja',
            'carrito': [{'id': self.servicio.id, 'cantidad': 1}],
            'pagos': [{'metodo': 'Efectivo USD', 'monto_ingresado': '25',
                       'equivalente_usd': '25'}],
        })
        self.assertEqual(resp.json()['status'], 'success')
        factura = Factura.objects.get(cedula_cliente='19000666')
        self.assertEqual(factura.estado, 'Pagada')
        self.assertEqual(factura.total, Decimal('25.00'))
        self.assertTrue(PagoFactura.objects.filter(factura=factura, sesion=self.sesion).exists())

    def test_precio_se_toma_del_catalogo_no_del_navegador(self):
        # Aunque el navegador mande otro precio, manda el del catálogo (25.00)
        self._cobrar({
            'cedula': '19000666', 'nombre': 'Cliente Prueba',
            'carrito': [{'id': self.servicio.id, 'cantidad': 1, 'precio': '0.01'}],
            'pagos': [{'metodo': 'Efectivo USD', 'monto_ingresado': '25', 'equivalente_usd': '25'}],
        })
        self.assertEqual(Factura.objects.get(cedula_cliente='19000666').total, Decimal('25.00'))

    def test_recibir_cobro_de_deuda_pendiente(self):
        # Una factura pendiente (ej. de farmacia) que la caja central cobra
        deuda = Factura.objects.create(nombre_cliente='Deudor', cedula_cliente='20000777',
                                       estado='Pendiente', total=Decimal('10.00'))
        resp = self._cobrar({
            'cedula': '20000777', 'nombre': 'Deudor',
            'carrito': [], 'facturas_pendientes': [deuda.id],
            'pagos': [{'metodo': 'Efectivo Bs', 'monto_ingresado': '400', 'equivalente_usd': '10'}],
        })
        self.assertEqual(resp.json()['status'], 'success')
        deuda.refresh_from_db()
        self.assertEqual(deuda.estado, 'Pagada')

    def test_cobro_sin_pagos_es_rechazado(self):
        resp = self._cobrar({
            'cedula': '19000666', 'nombre': 'Cliente Prueba',
            'carrito': [{'id': self.servicio.id, 'cantidad': 1}], 'pagos': [],
        })
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(Factura.objects.exists())

    def test_cobro_cedula_invalida_es_rechazado(self):
        resp = self._cobrar({
            'cedula': '999999999', 'nombre': 'Cliente Prueba',
            'carrito': [{'id': self.servicio.id, 'cantidad': 1}],
            'pagos': [{'metodo': 'Efectivo USD', 'monto_ingresado': '25', 'equivalente_usd': '25'}],
        })
        self.assertEqual(resp.status_code, 400)

    def test_metodo_de_pago_invalido_es_rechazado(self):
        resp = self._cobrar({
            'cedula': '19000666', 'nombre': 'Cliente Prueba',
            'carrito': [{'id': self.servicio.id, 'cantidad': 1}],
            'pagos': [{'metodo': 'Bitcoin', 'monto_ingresado': '25', 'equivalente_usd': '25'}],
        })
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(Factura.objects.exists())   # transacción revertida

    def test_cuentas_abiertas_agrupa_pendientes(self):
        Factura.objects.create(nombre_cliente='A', cedula_cliente='21000888',
                               estado='Pendiente', total=Decimal('5.00'))
        Factura.objects.create(nombre_cliente='A', cedula_cliente='21000888',
                               estado='Pendiente', total=Decimal('3.00'))
        resp = self.client.get(reverse('cuentas_abiertas'))
        cuentas = {c['cedula']: c for c in resp.json()['cuentas']}
        self.assertEqual(cuentas['21000888']['total'], 8.0)
        self.assertEqual(cuentas['21000888']['num_facturas'], 2)

    def test_obtener_deudas_paciente(self):
        f = Factura.objects.create(nombre_cliente='D', cedula_cliente='22000999',
                                   estado='Pendiente', total=Decimal('15.00'))
        DetalleFactura.objects.create(factura=f, departamento='Consulta',
                                      descripcion='Eco', cantidad=1, precio_unitario=Decimal('15.00'))
        resp = self.client.get(reverse('obtener_deudas_paciente', kwargs={'cedula': '22000999'}))
        data = resp.json()
        self.assertIn(f.id, data['facturas_ids'])
        self.assertEqual(len(data['deudas']), 1)


class TasaBcvCajaTests(BaseAdminTest):
    @patch('administracion.views.obtener_tasa_bcv', return_value=Decimal('42.00'))
    def test_caja_abre_sesion_con_tasa_de_api(self, _mock):
        resp = self.client.get(reverse('caja_central'))
        self.assertEqual(resp.context['tasa_bcv'], 42.00)
        self.assertTrue(SesionCaja.objects.filter(cajero=self.admin, estado='Abierta').exists())

    @patch('administracion.views.obtener_tasa_bcv', return_value=None)
    def test_caja_usa_ultima_tasa_si_api_falla(self, _mock):
        SesionCaja.objects.create(cajero=self.admin, tasa_bcv_dia=Decimal('39.00'), estado='Cerrada')
        resp = self.client.get(reverse('caja_central'))
        self.assertEqual(resp.context['tasa_bcv'], 39.00)   # respaldo


class CerrarCajaTests(BaseAdminTest):
    def test_cierre_suma_por_metodo_y_cierra_sesion(self):
        sesion = SesionCaja.objects.create(cajero=self.admin, tasa_bcv_dia=Decimal('40.00'), estado='Abierta')
        factura = Factura.objects.create(nombre_cliente='X', cedula_cliente='1', estado='Pagada')
        PagoFactura.objects.create(factura=factura, sesion=sesion, metodo='Efectivo USD',
                                   monto_moneda_original=Decimal('30'), monto_equivalente_usd=Decimal('30'))
        PagoFactura.objects.create(factura=factura, sesion=sesion, metodo='Zelle',
                                   monto_moneda_original=Decimal('20'), monto_equivalente_usd=Decimal('20'))
        resp = self.client.post(reverse('cerrar_caja'))
        sesion.refresh_from_db()
        self.assertEqual(sesion.estado, 'Cerrada')
        self.assertEqual(sesion.total_usd_efectivo, Decimal('30'))
        self.assertEqual(sesion.total_zelle, Decimal('20'))
        self.assertIsNotNone(sesion.fecha_cierre)


# ============================================================
# 7. Estadísticas (JSON, Excel, PDF)
# ============================================================
class EstadisticasTests(BaseAdminTest):
    def test_datos_estadisticas_devuelve_las_cuatro_secciones(self):
        resp = self.client.get(reverse('datos_estadisticas'))
        data = resp.json()
        for clave in ('morbilidad', 'flujo', 'medicamentos', 'examenes'):
            self.assertIn(clave, data)

    def test_exportar_excel_tipo_valido(self):
        resp = self.client.get(reverse('exportar_excel_estadisticas', kwargs={'tipo': 'morbilidad'}))
        self.assertEqual(resp.status_code, 200)
        self.assertIn('spreadsheetml', resp['Content-Type'])

    def test_exportar_excel_tipo_invalido_da_404(self):
        resp = self.client.get(reverse('exportar_excel_estadisticas', kwargs={'tipo': 'inventado'}))
        self.assertEqual(resp.status_code, 404)

    def test_pdf_estadisticas_renderiza(self):
        self.assertEqual(self.client.get(reverse('pdf_estadisticas')).status_code, 200)


# ============================================================
# 8. Historial (solo atendidos) + filtro fecha
# ============================================================
class HistorialCitasTests(BaseAdminTest):
    def setUp(self):
        super().setUp()
        self.px = Paciente.objects.create(nombres='Px', cedula='23001000', tipo_sangre='O+')
        self.atendida = Cita.objects.create(paciente=self.px, medico=self.medico,
                                            fecha='2026-07-01', hora=time(9, 0),
                                            motivo='X', estado='Atendido')
        self.pendiente = Cita.objects.create(paciente=self.px, medico=self.medico,
                                             fecha='2026-07-01', hora=time(10, 0),
                                             motivo='Y', estado='Pendiente')

    def test_historial_muestra_solo_atendidos(self):
        resp = self.client.get(reverse('historial_citas'))
        ids = [c.id for c in resp.context['citas']]
        self.assertIn(self.atendida.id, ids)
        self.assertNotIn(self.pendiente.id, ids)

    def test_historial_filtra_por_fecha(self):
        resp = self.client.get(reverse('historial_citas'), {'fecha': '2026-01-01'})
        self.assertEqual(len(list(resp.context['citas'])), 0)


# ============================================================
# 9. Personal: sala de espera, aprobar, rechazar
# ============================================================
class PersonalTests(BaseAdminTest):
    def setUp(self):
        super().setUp()
        self.pendiente = Usuario.objects.create_user(
            username='nuevo', email='nuevo@x.com', password='x', cedula='24001111',
            rol='medico', telefono='1', first_name='Nuevo', last_name='Empleado',
            is_active=False)

    def test_sala_espera_lista_inactivos(self):
        resp = self.client.get(reverse('sala_espera'))
        ids = [u.id for u in resp.context['usuarios_pendientes']]
        self.assertIn(self.pendiente.id, ids)

    def test_aprobar_activa_y_notifica(self):
        self.client.post(reverse('aprobar_usuario', kwargs={'usuario_id': self.pendiente.id}))
        self.pendiente.refresh_from_db()
        self.assertTrue(self.pendiente.is_active)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('nuevo@x.com', mail.outbox[0].to)

    def test_rechazar_elimina_usuario(self):
        self.client.post(reverse('rechazar_usuario', kwargs={'usuario_id': self.pendiente.id}))
        self.assertFalse(Usuario.objects.filter(id=self.pendiente.id).exists())

    def test_lista_personal_agrupa_por_rol(self):
        resp = self.client.get(reverse('lista_personal'))
        self.assertIn('medicos', resp.context['personal'])
        self.assertIn('farmacia', resp.context['personal'])


# ============================================================
# 10. Eliminar cita
# ============================================================
class EliminarCitaTests(BaseAdminTest):
    def test_eliminar_cita(self):
        px = Paciente.objects.create(nombres='Px', cedula='25001222', tipo_sangre='O+')
        cita = Cita.objects.create(paciente=px, medico=self.medico, fecha='2026-07-01',
                                   hora=time(9, 0), motivo='X')
        self.client.post(reverse('eliminar_cita', kwargs={'cita_id': cita.id}))
        self.assertFalse(Cita.objects.filter(id=cita.id).exists())


# ============================================================
# 11. Disponibilidad de horas
# ============================================================
class DisponibilidadTests(BaseAdminTest):
    def test_devuelve_horas_ocupadas(self):
        px = Paciente.objects.create(nombres='Px', cedula='26001333', tipo_sangre='O+')
        Cita.objects.create(paciente=px, medico=self.medico, fecha='2026-07-01',
                            hora=time(9, 0), motivo='X', estado='Pendiente')
        resp = self.client.get(reverse('verificar_disponibilidad'),
                               {'medico_id': self.medico.id, 'fecha': '2026-07-01'})
        self.assertIn('09:00', resp.json()['ocupadas'])

    def test_sin_parametros_devuelve_vacio(self):
        resp = self.client.get(reverse('verificar_disponibilidad'))
        self.assertEqual(resp.json()['ocupadas'], [])


# ============================================================
# 12. Perfil del usuario administración
# ============================================================
class PerfilAdminTests(BaseAdminTest):
    def test_ver_perfil(self):
        self.assertEqual(self.client.get(reverse('editar_perfil_admin')).status_code, 200)

    def test_editar_perfil(self):
        self.client.post(reverse('editar_perfil_admin'), {
            'nombre_completo': 'Sofia Marin', 'cedula': '12000444',
            'telefono': '04121112233', 'email': 'sofia@cruzroja.org',
        })
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.first_name, 'Sofia')
        self.assertEqual(self.admin.last_name, 'Marin')
        self.assertEqual(self.admin.email, 'sofia@cruzroja.org')
