"""
Tests del módulo medico.

Cubren lo que se espera que el médico pueda hacer:
- Guardar la historia clínica (cita agendada, paciente nuevo/emergencia y control).
- Enviar y exportar solicitudes, récipes y constancias (a lab, farmacia, PDF y correo).
- Ver estadísticas (morbilidad) y exportarlas.
- Editar su perfil (incluyendo firma y sello).
- Ver resultados cargados por laboratorio.
- Acceder al expediente y ver los gráficos de los controles.

NOTA sobre los "mocks": el envío por correo genera un PDF con Chromium (Playwright)
y abre un hilo; el procesamiento de firma/sello usa IA (rembg). En los tests NO
queremos ejecutar nada de eso de verdad —es lento y depende del navegador/IA—, así
que lo reemplazamos por un doble de prueba con @patch y solo verificamos que la
vista lo HAYA llamado con los datos correctos.
"""
import sys
import io
from datetime import time
from types import ModuleType
from unittest.mock import patch
from django.test import TestCase
from django.urls import reverse
from PIL import Image

from usuarios.models import Usuario
from administracion.models import Paciente, Medico, Cita
from medico.models import ExpedienteBase, ConsultaEvolucion, Recipe, ConstanciaMedica
from farmacia.models import OrdenFarmacia
from laboratorio.models import SolicitudExamen


def _png_subible(nombre='firma.png'):
    """Genera un archivo PNG válido en memoria para subir en los tests."""
    from django.core.files.uploadedfile import SimpleUploadedFile
    buffer = io.BytesIO()
    Image.new('RGB', (10, 10), 'white').save(buffer, format='PNG')
    return SimpleUploadedFile(nombre, buffer.getvalue(), content_type='image/png')


class MedicoTestBase(TestCase):
    """Setup común: un médico logueado con su perfil y un paciente registrado."""

    def setUp(self):
        self.user = Usuario.objects.create_user(
            username='drhouse', email='house@cruzroja.org', password='clave12345',
            cedula='5000', rol='medico', telefono='0412',
            first_name='Gregory', last_name='House',
        )
        self.medico = Medico.objects.create(
            usuario=self.user, nombre='Gregory House', especialidad='Internista',
        )
        self.paciente = Paciente.objects.create(
            nombres='Lisa Cuddy', cedula='12345678', tipo_sangre='O+',
            email='cuddy@correo.com',
        )
        self.client.login(email='house@cruzroja.org', password='clave12345')


# ===========================================================================
# 1. GUARDAR LA INFORMACIÓN DEL PACIENTE
# ===========================================================================
class AtenderCitaAgendadaTests(MedicoTestBase):
    """Guardar la historia de una cita agendada (atender_paciente)."""

    def setUp(self):
        super().setUp()
        self.cita = Cita.objects.create(
            paciente=self.paciente, medico=self.medico,
            hora=time(9, 0), motivo='Dolor de cabeza', estado='Pendiente',
        )

    def test_guarda_historia_y_marca_cita_atendida(self):
        resp = self.client.post(
            reverse('atender_paciente', args=[self.cita.id]),
            {'motivo': 'Cefalea', 'diagnostico': 'Migraña', 'plan': 'Reposo'},
        )
        self.assertEqual(resp.status_code, 302)
        self.cita.refresh_from_db()
        self.assertEqual(self.cita.estado, 'Atendido')
        self.assertEqual(ConsultaEvolucion.objects.filter(cita=self.cita).count(), 1)
        consulta = ConsultaEvolucion.objects.get(cita=self.cita)
        self.assertEqual(consulta.diagnostico, 'Migraña')

    def test_diagnostico_personalizado_usa_el_campo_otro(self):
        self.client.post(
            reverse('atender_paciente', args=[self.cita.id]),
            {'diagnostico': 'Otro - Especificar manualmente',
             'diagnostico_otro': 'Síndrome raro', 'plan': 'x'},
        )
        consulta = ConsultaEvolucion.objects.get(cita=self.cita)
        self.assertEqual(consulta.diagnostico, 'Síndrome raro')

    def test_no_puede_atender_cita_de_otro_medico(self):
        otro_user = Usuario.objects.create_user(
            username='wilson', email='wilson@cruzroja.org', password='x',
            cedula='6000', rol='medico', telefono='0412',
        )
        otro_medico = Medico.objects.create(usuario=otro_user, nombre='Wilson', especialidad='Onco')
        cita_ajena = Cita.objects.create(
            paciente=self.paciente, medico=otro_medico, hora=time(10, 0), motivo='x',
        )
        resp = self.client.post(reverse('atender_paciente', args=[cita_ajena.id]), {})
        self.assertEqual(resp.status_code, 403)


class HistoriaManualTests(MedicoTestBase):
    """Paciente nuevo (emergencia) y control (crear_historia_manual)."""

    def test_registra_paciente_nuevo_de_emergencia(self):
        self.client.post(reverse('crear_historia_manual'), {
            'tipo_registro': 'nuevo',
            'nuevo_nombre': 'Juan Emergencia',
            'nuevo_cedula': 'V-9.888.777',
            'nuevo_nacionalidad': 'V',
            'nuevo_sangre': 'A+',
            'diagnostico': 'Fractura',
        })
        nuevo = Paciente.objects.get(cedula='9888777')
        self.assertEqual(nuevo.nombres, 'Juan Emergencia')
        self.assertTrue(ConsultaEvolucion.objects.filter(expediente__paciente=nuevo).exists())

    def test_cedula_invalida_no_crea_paciente(self):
        self.client.post(reverse('crear_historia_manual'), {
            'tipo_registro': 'nuevo',
            'nuevo_nombre': 'Cédula Mala',
            'nuevo_cedula': '999999999',
            'nuevo_nacionalidad': 'V',
            'nuevo_sangre': 'A+',
        })
        self.assertFalse(Paciente.objects.filter(nombres='Cédula Mala').exists())

    def test_cedula_duplicada_no_crea_paciente(self):
        self.client.post(reverse('crear_historia_manual'), {
            'tipo_registro': 'nuevo',
            'nuevo_nombre': 'Duplicado',
            'nuevo_cedula': self.paciente.cedula,
            'nuevo_nacionalidad': 'V',
            'nuevo_sangre': 'O+',
        })
        self.assertFalse(Paciente.objects.filter(nombres='Duplicado').exists())

    def test_control_de_paciente_registrado_crea_consulta(self):
        self.client.post(reverse('crear_historia_manual'), {
            'tipo_registro': 'control',
            'paciente_control_id': self.paciente.id,
            'diagnostico': 'Control rutinario',
            'alergias': 'Penicilina',
        })
        self.assertTrue(
            ConsultaEvolucion.objects.filter(expediente__paciente=self.paciente).exists()
        )
        exp = ExpedienteBase.objects.get(paciente=self.paciente)
        self.assertEqual(exp.alergias, 'Penicilina')


# ===========================================================================
# 2. ENVIAR / EXPORTAR SOLICITUDES DE LABORATORIO
# ===========================================================================
class SolicitarExamenesTests(MedicoTestBase):

    def test_envio_a_laboratorio_entra_en_la_cola(self):
        self.client.post(reverse('solicitar_examenes'), {
            'tipo_paciente': 'registrado',
            'paciente_id': self.paciente.id,
            'examenes': ['Hematología', 'Glicemia'],
            'accion': 'enviar_lab',
        })
        orden = SolicitudExamen.objects.get(paciente=self.paciente)
        self.assertEqual(orden.estado, 'Pendiente')
        self.assertTrue(orden.procesar_en_lab)
        self.assertIn('Hematología', orden.examenes_solicitados)

    def test_orden_en_pdf_es_externa_y_no_entra_a_la_cola(self):
        self.client.post(reverse('solicitar_examenes'), {
            'tipo_paciente': 'registrado',
            'paciente_id': self.paciente.id,
            'examenes': ['Glicemia'],
            'accion': 'generar_pdf',
        })
        orden = SolicitudExamen.objects.get(paciente=self.paciente)
        self.assertEqual(orden.estado, 'Externa')
        self.assertFalse(orden.procesar_en_lab)

    def test_sin_examenes_no_crea_orden(self):
        self.client.post(reverse('solicitar_examenes'), {
            'tipo_paciente': 'registrado',
            'paciente_id': self.paciente.id,
            'accion': 'enviar_lab',
        })
        self.assertFalse(SolicitudExamen.objects.exists())

    @patch('medico.views.enviar_documento_pdf_async')
    def test_envio_por_correo_dispara_el_envio(self, mock_envio):
        self.client.post(reverse('solicitar_examenes'), {
            'tipo_paciente': 'registrado',
            'paciente_id': self.paciente.id,
            'examenes': ['Glicemia'],
            'accion': 'enviar_correo',
            'correo_paciente': 'destino@correo.com',
        })
        self.assertTrue(mock_envio.called)
        _, kwargs = mock_envio.call_args
        self.assertEqual(kwargs['destinatario'], 'destino@correo.com')
        self.assertEqual(SolicitudExamen.objects.get().estado, 'Externa')


# ===========================================================================
# 3. ENVIAR / EXPORTAR RÉCIPES
# ===========================================================================
class CrearRecipeTests(MedicoTestBase):

    def test_enviar_a_farmacia_crea_recipe_y_orden(self):
        self.client.post(reverse('crear_recipe'), {
            'paciente_id': self.paciente.id,
            'medicamentos': 'Paracetamol 500mg',
            'indicaciones': '1 cada 8h',
            'accion': 'enviar_farmacia',
        })
        self.assertEqual(Recipe.objects.count(), 1)
        self.assertEqual(OrdenFarmacia.objects.count(), 1)
        orden = OrdenFarmacia.objects.get()
        self.assertEqual(orden.estado, 'Pendiente')
        self.assertIn('Paracetamol', orden.receta_medica_texto)

    def test_exportar_pdf_crea_recipe(self):
        self.client.post(reverse('crear_recipe'), {
            'paciente_id': self.paciente.id,
            'medicamentos': 'Ibuprofeno',
            'indicaciones': 'cada 12h',
            'accion': 'exportar_pdf',
        })
        self.assertEqual(Recipe.objects.count(), 1)

    def test_cedula_manual_invalida_no_crea_recipe(self):
        self.client.post(reverse('crear_recipe'), {
            'nombre_manual': 'Paciente Externo',
            'cedula_manual': '999999999',
            'medicamentos': 'x', 'indicaciones': 'y',
            'accion': 'exportar_pdf',
        })
        self.assertFalse(Recipe.objects.exists())

    @patch('medico.views.enviar_documento_pdf_async')
    def test_enviar_por_correo_dispara_el_envio(self, mock_envio):
        self.client.post(reverse('crear_recipe'), {
            'paciente_id': self.paciente.id,
            'medicamentos': 'Amoxicilina', 'indicaciones': 'cada 8h',
            'accion': 'enviar_correo',
            'correo_paciente': 'paciente@correo.com',
        })
        self.assertTrue(mock_envio.called)
        self.assertEqual(Recipe.objects.count(), 1)


# ===========================================================================
# 4. CONSTANCIAS
# ===========================================================================
class ConstanciaTests(MedicoTestBase):

    def test_crea_constancia_con_codigo_de_verificacion(self):
        self.client.post(
            reverse('generar_constancia', args=[self.paciente.uuid]),
            {'motivo_texto': 'Reposo por 3 días', 'accion': 'ver'},
        )
        self.assertEqual(ConstanciaMedica.objects.count(), 1)
        constancia = ConstanciaMedica.objects.get()
        self.assertTrue(constancia.codigo_verificacion)

    def test_motivo_vacio_no_crea_constancia(self):
        self.client.post(
            reverse('generar_constancia', args=[self.paciente.uuid]),
            {'motivo_texto': '   ', 'accion': 'ver'},
        )
        self.assertFalse(ConstanciaMedica.objects.exists())

    @patch('medico.views.enviar_documento_pdf_async')
    def test_constancia_por_correo_dispara_el_envio(self, mock_envio):
        self.client.post(
            reverse('generar_constancia', args=[self.paciente.uuid]),
            {'motivo_texto': 'Reposo', 'accion': 'enviar_correo',
             'correo_paciente': 'px@correo.com'},
        )
        self.assertTrue(mock_envio.called)
        self.assertEqual(ConstanciaMedica.objects.count(), 1)


# ===========================================================================
# 5. EXPORTAR EN PDF (autorización de la historia clínica)
# ===========================================================================
class PdfHistoriaTests(MedicoTestBase):

    def setUp(self):
        super().setUp()
        exp, _ = ExpedienteBase.objects.get_or_create(paciente=self.paciente)
        self.historia = ConsultaEvolucion.objects.create(
            expediente=exp, medico=self.medico, diagnostico='Gripe',
        )

    def test_otro_medico_no_puede_descargar_la_historia(self):
        otro_user = Usuario.objects.create_user(
            username='otro', email='otro@cruzroja.org', password='x',
            cedula='7000', rol='medico', telefono='0412',
        )
        Medico.objects.create(usuario=otro_user, nombre='Otro', especialidad='x')
        self.client.logout()
        self.client.login(email='otro@cruzroja.org', password='x')
        resp = self.client.get(reverse('pdf_historia', args=[self.historia.id]))
        self.assertEqual(resp.status_code, 403)


# ===========================================================================
# 6. VER RESULTADOS CARGADOS POR LABORATORIO
# ===========================================================================
class ResultadosExamenesTests(MedicoTestBase):

    def test_solo_muestra_resultados_realizados_del_medico(self):
        SolicitudExamen.objects.create(
            paciente=self.paciente, nombre_paciente='Lisa Cuddy',
            cedula_paciente='12345678', medico=self.medico,
            examenes_solicitados='Hematología', estado='Realizado',
        )
        SolicitudExamen.objects.create(
            paciente=self.paciente, nombre_paciente='Lisa Cuddy',
            cedula_paciente='12345678', medico=self.medico,
            examenes_solicitados='Glicemia', estado='Pendiente',
        )
        resp = self.client.get(reverse('resultados_examenes'))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.context['resultados']), 1)

    def test_busqueda_filtra_por_cedula(self):
        SolicitudExamen.objects.create(
            nombre_paciente='Otro Px', cedula_paciente='55555555',
            medico=self.medico, examenes_solicitados='X', estado='Realizado',
        )
        SolicitudExamen.objects.create(
            nombre_paciente='Lisa', cedula_paciente='12345678',
            medico=self.medico, examenes_solicitados='Y', estado='Realizado',
        )
        resp = self.client.get(reverse('resultados_examenes'), {'q': '55555555'})
        self.assertEqual(len(resp.context['resultados']), 1)


# ===========================================================================
# 7. EXPEDIENTE Y GRÁFICOS DE CONTROLES
# ===========================================================================
class ExpedienteTests(MedicoTestBase):

    def test_expediente_arma_datos_de_graficos(self):
        exp, _ = ExpedienteBase.objects.get_or_create(paciente=self.paciente)
        ConsultaEvolucion.objects.create(
            expediente=exp, medico=self.medico,
            tension_arterial='120/80', peso='70.5',
        )
        resp = self.client.get(
            reverse('ver_expediente_unificado', args=[self.paciente.uuid])
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn('120', resp.context['sis_json'])
        self.assertIn('80', resp.context['dia_json'])
        self.assertIn('70.5', resp.context['peso_json'])


# ===========================================================================
# 8. ESTADÍSTICAS (MORBILIDAD)
# ===========================================================================
class EstadisticasTests(MedicoTestBase):

    def test_api_estadisticas_devuelve_json_con_las_secciones(self):
        exp, _ = ExpedienteBase.objects.get_or_create(paciente=self.paciente)
        ConsultaEvolucion.objects.create(expediente=exp, medico=self.medico, diagnostico='Gripe')
        ConsultaEvolucion.objects.create(expediente=exp, medico=self.medico, diagnostico='Gripe')
        resp = self.client.get(reverse('api_estadisticas_medico'))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn('medico', data)
        self.assertIn('tendencia', data)
        self.assertIn('global', data)
        self.assertIn('Gripe', data['medico']['labels'])

    def test_exportar_morbilidad_devuelve_excel(self):
        exp, _ = ExpedienteBase.objects.get_or_create(paciente=self.paciente)
        ConsultaEvolucion.objects.create(expediente=exp, medico=self.medico, diagnostico='Asma')
        resp = self.client.get(reverse('exportar_morbilidad_excel'), {'tipo': 'personal'})
        self.assertEqual(resp.status_code, 200)
        self.assertIn('spreadsheetml', resp['Content-Type'])
        self.assertIn('attachment', resp['Content-Disposition'])


# ===========================================================================
# 9. EDITAR PERFIL (incluye firma y sello)
# ===========================================================================
class EditarPerfilTests(MedicoTestBase):

    def test_actualiza_datos_del_perfil(self):
        self.client.post(reverse('editar_perfil_medico'), {
            'nombre_completo': 'Gregory Modificado',
            'cedula': '5000', 'mpps': 'MPPS-123', 'cm': 'CM-456',
        })
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, 'Gregory')
        self.assertEqual(self.user.last_name, 'Modificado')
        self.assertEqual(self.user.mpps, 'MPPS-123')

    def test_foto_no_imagen_es_rechazada(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        basura = SimpleUploadedFile('foto.png', b'esto no es una imagen', content_type='image/png')
        resp = self.client.post(reverse('editar_perfil_medico'), {
            'nombre_completo': 'Gregory House', 'foto_perfil': basura,
        })
        self.assertEqual(resp.status_code, 302)
        self.medico.refresh_from_db()
        self.assertFalse(self.medico.foto_perfil)


class FirmaSelloTests(MedicoTestBase):
    """
    cargar_firma_sello usa rembg (IA para quitar el fondo). Inyectamos un módulo
    'rembg' falso en sys.modules para que 'from rembg import remove' funcione en
    cualquier entorno y NO ejecute la IA real: el doble solo devuelve los bytes.
    """

    def setUp(self):
        super().setUp()
        fake_rembg = ModuleType('rembg')
        fake_rembg.remove = lambda data: data
        self._patcher = patch.dict(sys.modules, {'rembg': fake_rembg})
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    def test_carga_firma_y_sello(self):
        resp = self.client.post(reverse('cargar_firma_sello'), {
            'firma': _png_subible('firma.png'),
            'sello': _png_subible('sello.png'),
        })
        self.assertEqual(resp.status_code, 302)
        self.medico.refresh_from_db()
        self.assertTrue(self.medico.firma)
        self.assertTrue(self.medico.sello)

    def test_eliminar_firma(self):
        self.client.post(reverse('cargar_firma_sello'), {'firma': _png_subible()})
        self.medico.refresh_from_db()
        self.assertTrue(self.medico.firma)
        self.client.post(reverse('cargar_firma_sello'), {'eliminar_firma': '1'})
        self.medico.refresh_from_db()
        self.assertFalse(self.medico.firma)


# ===========================================================================
# 10. CONTROL DE ACCESO (que solo un médico entre)
# ===========================================================================
class AccesoMedicoTests(TestCase):

    def test_usuario_de_otro_rol_no_entra_al_dashboard(self):
        Usuario.objects.create_user(
            username='lab', email='lab@cruzroja.org', password='x',
            cedula='8000', rol='laboratorio', telefono='0412',
        )
        self.client.login(email='lab@cruzroja.org', password='x')
        resp = self.client.get(reverse('dashboard_medico'))
        self.assertEqual(resp.status_code, 403)
