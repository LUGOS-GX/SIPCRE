"""
Tests del módulo laboratorio.

Cubren lo que se espera del laboratorio:
- Recibir y procesar órdenes (carga de resultados + descuento de reactivos).
- Subir un archivo de resultados (PDF/imagen) y rechazar archivos inválidos.
- Enviar los resultados por correo (selección de destinatario y adjunto).
- Ver órdenes y resultados en el historial.
- Que el buscador funcione en la bandeja (activas) y en el historial.
- Ver y exportar estadísticas.
- Ver y modificar el perfil.

Notas técnicas:
- Usamos SQLite en memoria (settings de prueba), no toca la BD real.
- El render del PDF (Chromium/Playwright) y el envío de correo en hilo se
  "mockean" (se sustituyen por dobles de prueba) para que los tests sean
  rápidos, deterministas y no dependan del navegador ni de internet.
"""
from datetime import time
from unittest.mock import patch
from django.test import TestCase
from django.urls import reverse
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile

from usuarios.models import Usuario
from administracion.models import Paciente, Medico
from farmacia.models import Medicamento, LoteMedicamento, MovimientoInventario
from laboratorio.models import (
    SolicitudExamen, ExamenCatalogo, ParametroExamen, ResultadoDetalle,
)
from laboratorio.views import enviar_correo_resultados_async


def _crear_laboratorista():
    return Usuario.objects.create_user(
        username='lab', email='lab@cruzroja.org', password='clave12345',
        cedula='999', rol='laboratorio', telefono='0412',
    )


# PDF falso para no invocar Chromium en los tests
_PDF_FALSO = b'%PDF-1.4 contenido de prueba'


class BaseLabTest(TestCase):
    """Crea un laboratorista logueado y datos comunes."""
    def setUp(self):
        self.lab = _crear_laboratorista()
        self.client.login(email='lab@cruzroja.org', password='clave12345')
        self.paciente = Paciente.objects.create(
            nombres='Juan Perez', cedula='12345678', tipo_sangre='O+',
            email='juan@example.com',
        )


class ProcesarOrdenTests(BaseLabTest):
    """Recibir una orden y cargar resultados estructurados."""

    def setUp(self):
        super().setUp()
        # Catálogo: examen con un parámetro y un reactivo a descontar
        self.reactivo = Medicamento.objects.create(
            nombre='Reactivo Glucosa', concentracion='-', presentacion='Kit',
            stock_actual=50, precio=1,
        )
        from datetime import date, timedelta
        LoteMedicamento.objects.create(
            medicamento=self.reactivo, numero_lote='#001',
            cantidad_ingresada=50, cantidad_actual=50,
            fecha_vencimiento=date.today() + timedelta(days=180),
        )
        self.examen = ExamenCatalogo.objects.create(
            nombre='Glicemia', activo=True,
            reactivo_necesario=self.reactivo, cantidad_reactivo=2,
        )
        self.param = ParametroExamen.objects.create(
            examen=self.examen, nombre='Glucosa', unidad_medida='mg/dL',
            rango_minimo=70, rango_maximo=110,
        )
        self.orden = SolicitudExamen.objects.create(
            paciente=self.paciente, nombre_paciente='Juan Perez',
            cedula_paciente='12345678', examenes_solicitados='Glicemia',
            procesar_en_lab=True, estado='Pendiente',
        )
        self.url = reverse('detalle_orden', kwargs={'orden_id': self.orden.id})

    @patch('laboratorio.views.threading.Thread')          # no lanzar hilo real
    @patch('laboratorio.views.render_pdf_desde_template', return_value=_PDF_FALSO)
    def test_guardar_resultados_marca_realizado_y_guarda_valor(self, mock_pdf, mock_hilo):
        resp = self.client.post(self.url, {
            'guardar_resultados': '1',
            f'param_{self.param.id}': '95',
        })
        self.orden.refresh_from_db()
        self.assertEqual(self.orden.estado, 'Realizado')
        self.assertIsNotNone(self.orden.fecha_resultado)
        detalle = ResultadoDetalle.objects.get(orden=self.orden, parametro=self.param)
        self.assertEqual(detalle.valor_obtenido, '95')
        self.assertFalse(detalle.es_anormal)   # 95 está dentro de 70-110

    @patch('laboratorio.views.threading.Thread')
    @patch('laboratorio.views.render_pdf_desde_template', return_value=_PDF_FALSO)
    def test_valor_fuera_de_rango_se_marca_anormal(self, mock_pdf, mock_hilo):
        self.client.post(self.url, {
            'guardar_resultados': '1',
            f'param_{self.param.id}': '250',   # > 110
        })
        detalle = ResultadoDetalle.objects.get(orden=self.orden, parametro=self.param)
        self.assertTrue(detalle.es_anormal)

    @patch('laboratorio.views.threading.Thread')
    @patch('laboratorio.views.render_pdf_desde_template', return_value=_PDF_FALSO)
    def test_procesar_descuenta_reactivo_del_inventario(self, mock_pdf, mock_hilo):
        self.client.post(self.url, {
            'guardar_resultados': '1',
            f'param_{self.param.id}': '95',
        })
        self.reactivo.refresh_from_db()
        self.assertEqual(self.reactivo.stock_actual, 48)   # 50 - 2
        self.assertTrue(
            MovimientoInventario.objects.filter(
                medicamento=self.reactivo, tipo_movimiento='SALIDA').exists()
        )

    @patch('laboratorio.views.threading.Thread')
    @patch('laboratorio.views.render_pdf_desde_template', return_value=_PDF_FALSO)
    def test_reactivo_sin_stock_suficiente_no_bloquea_resultado(self, mock_pdf, mock_hilo):
        self.reactivo.stock_actual = 1   # menos que los 2 requeridos
        self.reactivo.save()
        self.client.post(self.url, {
            'guardar_resultados': '1',
            f'param_{self.param.id}': '95',
        })
        self.orden.refresh_from_db()
        self.reactivo.refresh_from_db()
        self.assertEqual(self.orden.estado, 'Realizado')   # el resultado igual se guarda
        self.assertEqual(self.reactivo.stock_actual, 1)     # no se descontó


class SubirArchivoResultadosTests(BaseLabTest):
    """Escenario B: subir un PDF/imagen como resultado."""

    def setUp(self):
        super().setUp()
        self.orden = SolicitudExamen.objects.create(
            paciente=self.paciente, nombre_paciente='Juan Perez',
            cedula_paciente='12345678', examenes_solicitados='Perfil 20',
            procesar_en_lab=True, estado='Pendiente',
        )
        self.url = reverse('detalle_orden', kwargs={'orden_id': self.orden.id})

    @patch('laboratorio.views.threading.Thread')
    def test_subir_pdf_valido_marca_realizado(self, mock_hilo):
        pdf = SimpleUploadedFile('res.pdf', b'%PDF-1.4 datos', content_type='application/pdf')
        self.client.post(self.url, {'subir_pdf': '1', 'archivo_resultados': pdf})
        self.orden.refresh_from_db()
        self.assertEqual(self.orden.estado, 'Realizado')
        self.assertTrue(self.orden.resultados_archivo)

    @patch('laboratorio.views.threading.Thread')
    def test_subir_archivo_invalido_es_rechazado(self, mock_hilo):
        falso = SimpleUploadedFile('virus.html', b'<html>no soy pdf</html>', content_type='text/html')
        self.client.post(self.url, {'subir_pdf': '1', 'archivo_resultados': falso})
        self.orden.refresh_from_db()
        self.assertEqual(self.orden.estado, 'Pendiente')   # no cambió
        self.assertFalse(self.orden.resultados_archivo)


class CorreoResultadosTests(BaseLabTest):
    """Envío de resultados por correo (probamos la función directamente)."""

    def setUp(self):
        super().setUp()
        self.medico = Medico.objects.create(nombre='Dr. House', especialidad='General')

    @patch('django.db.connection.close')   # el hilo cierra la conexión; en test la dejamos
    def test_envia_al_correo_del_paciente_con_adjunto(self, _mock_close):
        orden = SolicitudExamen.objects.create(
            paciente=self.paciente, nombre_paciente='Juan Perez',
            cedula_paciente='12345678', correo_paciente='juan@example.com',
            examenes_solicitados='Glicemia', medico=self.medico,
            estado='Realizado',
        )
        enviar_correo_resultados_async(orden.id, _PDF_FALSO)
        self.assertEqual(len(mail.outbox), 1)
        correo = mail.outbox[0]
        self.assertEqual(correo.to, ['juan@example.com'])
        self.assertIn(f'#{orden.id:05d}', correo.subject)
        self.assertEqual(len(correo.attachments), 1)   # PDF adjunto

    @patch('django.db.connection.close')
    def test_sin_destinatario_no_envia(self, _mock_close):
        orden = SolicitudExamen.objects.create(
            nombre_paciente='Sin Correo', cedula_paciente='000',
            correo_paciente=None, examenes_solicitados='Glicemia',
            estado='Realizado',
        )
        enviar_correo_resultados_async(orden.id, _PDF_FALSO)
        self.assertEqual(len(mail.outbox), 0)

    @patch('django.db.connection.close')
    def test_adjunto_muy_pesado_usa_plantilla_presencial(self, _mock_close):
        orden = SolicitudExamen.objects.create(
            paciente=self.paciente, nombre_paciente='Juan Perez',
            cedula_paciente='12345678', correo_paciente='juan@example.com',
            examenes_solicitados='Glicemia', estado='Realizado',
        )
        pdf_pesado = b'%PDF' + b'0' * (16 * 1024 * 1024)   # > 15 MB
        enviar_correo_resultados_async(orden.id, pdf_pesado)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(len(mail.outbox[0].attachments), 0)   # sin adjunto, se retira en sitio


class HistorialYBuscadorTests(BaseLabTest):
    """El dashboard muestra activas/historial y el buscador filtra ambos."""

    def setUp(self):
        super().setUp()
        self.otro_px = Paciente.objects.create(
            nombres='Maria Lopez', cedula='55667788', tipo_sangre='A+',
        )
        # Activa (pendiente) de Juan
        self.activa = SolicitudExamen.objects.create(
            paciente=self.paciente, nombre_paciente='Juan Perez',
            cedula_paciente='12345678', examenes_solicitados='Hematología',
            procesar_en_lab=True, estado='Pendiente',
        )
        # Historial (realizada) de Maria
        self.realizada = SolicitudExamen.objects.create(
            paciente=self.otro_px, nombre_paciente='Maria Lopez',
            cedula_paciente='55667788', examenes_solicitados='Uroanálisis',
            procesar_en_lab=True, estado='Realizado',
        )
        self.url = reverse('dashboard_lab')

    def test_orden_pendiente_aparece_en_activas(self):
        resp = self.client.get(self.url)
        ids_activas = [o.id for o in resp.context['page_activas']]
        self.assertIn(self.activa.id, ids_activas)
        self.assertNotIn(self.realizada.id, ids_activas)

    def test_orden_realizada_aparece_en_historial(self):
        resp = self.client.get(self.url)
        ids_hist = [o.id for o in resp.context['page_historial']]
        self.assertIn(self.realizada.id, ids_hist)

    def test_buscador_filtra_activas_por_nombre(self):
        resp = self.client.get(self.url, {'q': 'Juan'})
        ids_activas = [o.id for o in resp.context['page_activas']]
        self.assertIn(self.activa.id, ids_activas)

    def test_buscador_filtra_historial_por_cedula(self):
        resp = self.client.get(self.url, {'q': '55667788'})
        ids_hist = [o.id for o in resp.context['page_historial']]
        self.assertIn(self.realizada.id, ids_hist)

    def test_buscador_que_no_coincide_no_devuelve_nada(self):
        resp = self.client.get(self.url, {'q': 'ZZZNoExiste'})
        self.assertEqual(len(resp.context['page_activas']), 0)
        self.assertEqual(len(resp.context['page_historial']), 0)

    def test_orden_externa_no_aparece_en_la_bandeja(self):
        SolicitudExamen.objects.create(
            nombre_paciente='Externo', cedula_paciente='111',
            examenes_solicitados='Glicemia', procesar_en_lab=False,
            estado='Externa',
        )
        resp = self.client.get(self.url)
        nombres = [o.nombre_paciente for o in resp.context['page_activas']]
        self.assertNotIn('Externo', nombres)


class CancelarOrdenTests(BaseLabTest):
    def test_cancelar_orden_pendiente(self):
        orden = SolicitudExamen.objects.create(
            nombre_paciente='Juan', cedula_paciente='12345678',
            examenes_solicitados='Glicemia', estado='Pendiente',
        )
        url = reverse('cancelar_orden_lab', kwargs={'orden_id': orden.id})
        self.client.post(url)
        orden.refresh_from_db()
        self.assertEqual(orden.estado, 'Cancelada')
        self.assertIsNotNone(orden.fecha_resultado)


class EstadisticasTests(BaseLabTest):
    def setUp(self):
        super().setUp()
        for _ in range(3):
            SolicitudExamen.objects.create(
                nombre_paciente='X', cedula_paciente='1',
                examenes_solicitados='Glicemia, Hematología',
                procesar_en_lab=True, estado='Realizado',
            )

    def test_api_estadisticas_devuelve_json_con_top_examenes(self):
        resp = self.client.get(reverse('api_estadisticas_laboratorio'))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn('top_examenes', data)
        self.assertIn('tendencia', data)
        self.assertIn('Glicemia', data['top_examenes']['labels'])

    def test_exportar_excel_devuelve_archivo_descargable(self):
        resp = self.client.get(reverse('exportar_estadisticas_lab_excel'))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp['Content-Type'],
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        self.assertIn('attachment', resp['Content-Disposition'])


class PerfilLabTests(BaseLabTest):
    def test_ver_perfil(self):
        resp = self.client.get(reverse('editar_perfil_lab'))
        self.assertEqual(resp.status_code, 200)

    def test_modificar_perfil(self):
        self.client.post(reverse('editar_perfil_lab'), {
            'nombre_completo': 'Carlos Ramirez',
            'cedula': '20111222',
            'telefono': '04141234567',
        })
        self.lab.refresh_from_db()
        self.assertEqual(self.lab.first_name, 'Carlos')
        self.assertEqual(self.lab.last_name, 'Ramirez')
        self.assertEqual(self.lab.cedula, '20111222')
        self.assertEqual(self.lab.telefono, '04141234567')
