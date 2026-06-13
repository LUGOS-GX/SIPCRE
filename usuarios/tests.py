"""
Tests del módulo usuarios.

NOTA: reemplaza por completo el usuarios/tests.py anterior. Incluye los tests
del decorador rol_requerido de la 1ª sesión, más registro (médico y personal),
selección/asignación de rol, validaciones de cédula/teléfono/contraseña,
inicio y cierre de sesión, y el modelo Usuario.
"""
from django.test import TestCase
from django.urls import reverse

from usuarios.models import Usuario
from administracion.models import Medico


def _crear_usuario(rol='farmacia', email=None, activo=True, cedula='100'):
    return Usuario.objects.create_user(
        username=email or f'{rol}@x.com', email=email or f'{rol}@x.com',
        password='ClaveSegura123', cedula=cedula, rol=rol, telefono='0412-1234567',
        is_active=activo,
    )


# ============================================================
# 1. Registro de personal (admin / farmacia / laboratorio)
# ============================================================
class RegistroPersonalTests(TestCase):
    def _datos_validos(self, **extra):
        datos = {
            'email': 'nuevo@x.com', 'first_name': 'Nuevo', 'last_name': 'Empleado',
            'nacionalidad': 'V', 'cedula_numero': '12345678',
            'codigo_area': '0414', 'telefono_numero': '1234567',
            'password1': 'ClaveSegura123', 'password2': 'ClaveSegura123',
        }
        datos.update(extra)
        return datos

    def test_registro_farmacia_crea_usuario_congelado(self):
        url = reverse('registro_personal', kwargs={'rol_solicitado': 'farmacia'})
        resp = self.client.post(url, self._datos_validos())
        user = Usuario.objects.get(email='nuevo@x.com')
        self.assertEqual(user.rol, 'farmacia')          # rol asignado por el form
        self.assertFalse(user.is_active)                # congelado hasta aprobación
        self.assertEqual(user.username, 'nuevo@x.com')  # username = email
        self.assertEqual(user.cedula, 'V-12345678')     # cédula unida
        self.assertEqual(user.telefono, '0414-1234567') # teléfono unido
        self.assertRedirects(resp, reverse('login'))

    def test_registro_admin_asigna_rol_admin(self):
        url = reverse('registro_personal', kwargs={'rol_solicitado': 'admin'})
        self.client.post(url, self._datos_validos())
        self.assertEqual(Usuario.objects.get(email='nuevo@x.com').rol, 'admin')

    def test_registro_laboratorio_asigna_rol_lab(self):
        url = reverse('registro_personal', kwargs={'rol_solicitado': 'laboratorio'})
        self.client.post(url, self._datos_validos())
        self.assertEqual(Usuario.objects.get(email='nuevo@x.com').rol, 'laboratorio')

    def test_rol_inventado_en_url_redirige_a_seleccion(self):
        url = reverse('registro_personal', kwargs={'rol_solicitado': 'hacker'})
        resp = self.client.get(url)
        self.assertRedirects(resp, reverse('seleccion_rol'))


# ============================================================
# 2. Registro médico (crea también la ficha en administración)
# ============================================================
class RegistroMedicoTests(TestCase):
    def test_registro_medico_crea_usuario_y_ficha(self):
        url = reverse('registro_medico')
        resp = self.client.post(url, {
            'email': 'doc@x.com', 'first_name': 'Greg', 'last_name': 'House',
            'cm': '12345', 'especialidad': 'Cardiología', 'mpps': '999',
            'nacionalidad': 'V', 'cedula_numero': '12345678',
            'codigo_area': '0414', 'telefono_numero': '1234567',
            'password1': 'ClaveSegura123', 'password2': 'ClaveSegura123',
        })
        user = Usuario.objects.get(email='doc@x.com')
        self.assertEqual(user.rol, 'medico')
        self.assertFalse(user.is_active)
        # Se crea la ficha de Medico ligada al usuario
        ficha = Medico.objects.get(usuario=user)
        self.assertEqual(ficha.especialidad, 'Cardiología')
        self.assertRedirects(resp, reverse('login'))


# ============================================================
# 3. Validaciones del registro (cédula, teléfono, contraseña)
# ============================================================
class ValidacionesRegistroTests(TestCase):
    def setUp(self):
        self.url = reverse('registro_personal', kwargs={'rol_solicitado': 'farmacia'})

    def _post(self, **extra):
        datos = {
            'email': 'v@x.com', 'first_name': 'A', 'last_name': 'B',
            'nacionalidad': 'V', 'cedula_numero': '12345678',
            'codigo_area': '0414', 'telefono_numero': '1234567',
            'password1': 'ClaveSegura123', 'password2': 'ClaveSegura123',
        }
        datos.update(extra)
        return self.client.post(self.url, datos)

    def _no_creado(self):
        return not Usuario.objects.filter(email='v@x.com').exists()

    def test_cedula_con_letras_rechazada(self):
        self._post(cedula_numero='12AB5678')
        self.assertTrue(self._no_creado())

    def test_cedula_muy_corta_rechazada(self):
        self._post(cedula_numero='123')      # < 6 dígitos
        self.assertTrue(self._no_creado())

    def test_telefono_largo_incorrecto_rechazado(self):
        self._post(telefono_numero='12345')  # != 7 dígitos
        self.assertTrue(self._no_creado())

    def test_contrasenas_no_coinciden(self):
        self._post(password2='Otra12345Distinta')
        self.assertTrue(self._no_creado())

    def test_contrasena_muy_corta(self):
        self._post(password1='Ab1', password2='Ab1')
        self.assertTrue(self._no_creado())

    def test_contrasena_solo_numerica_rechazada(self):
        self._post(password1='987654321', password2='987654321')
        self.assertTrue(self._no_creado())

    def test_cedula_duplicada_rechazada(self):
        _crear_usuario(email='ya@x.com', cedula='V-12345678')
        self._post()   # misma cédula V-12345678
        self.assertTrue(self._no_creado())


# ============================================================
# 4. Inicio y cierre de sesión
# ============================================================
class LoginLogoutTests(TestCase):
    def test_login_correcto_redirige_segun_rol(self):
        _crear_usuario(rol='farmacia', email='f@x.com')
        resp = self.client.post(reverse('login'),
                                {'username': 'f@x.com', 'password': 'ClaveSegura123'})
        self.assertRedirects(resp, reverse('dashboard_farmacia'),
                             fetch_redirect_response=False)

    def test_login_rol_admin_va_a_su_dashboard(self):
        _crear_usuario(rol='admin', email='a@x.com')
        resp = self.client.post(reverse('login'),
                                {'username': 'a@x.com', 'password': 'ClaveSegura123'})
        self.assertRedirects(resp, reverse('dashboard_admin'),
                             fetch_redirect_response=False)

    def test_login_contrasena_incorrecta_no_inicia_sesion(self):
        _crear_usuario(rol='farmacia', email='f@x.com')
        resp = self.client.post(reverse('login'),
                                {'username': 'f@x.com', 'password': 'malisima'})
        self.assertEqual(resp.status_code, 200)        # re-renderiza el login
        self.assertFalse(resp.wsgi_request.user.is_authenticated)

    def test_usuario_congelado_no_puede_entrar(self):
        _crear_usuario(rol='farmacia', email='congelado@x.com', activo=False)
        resp = self.client.post(reverse('login'),
                                {'username': 'congelado@x.com', 'password': 'ClaveSegura123'})
        self.assertFalse(resp.wsgi_request.user.is_authenticated)

    def test_logout_cierra_sesion(self):
        _crear_usuario(rol='farmacia', email='f@x.com')
        self.client.login(email='f@x.com', password='ClaveSegura123')
        resp = self.client.get(reverse('logout'))
        self.assertRedirects(resp, reverse('landing_page'), fetch_redirect_response=False)
        self.assertFalse(resp.wsgi_request.user.is_authenticated)


# ============================================================
# 5. Landing y selección de rol
# ============================================================
class NavegacionTests(TestCase):
    def test_landing_anonimo_renderiza(self):
        self.assertEqual(self.client.get(reverse('landing_page')).status_code, 200)

    def test_landing_autenticado_redirige_a_dashboard(self):
        _crear_usuario(rol='laboratorio', email='l@x.com')
        self.client.login(email='l@x.com', password='ClaveSegura123')
        resp = self.client.get(reverse('landing_page'))
        self.assertRedirects(resp, reverse('dashboard_lab'), fetch_redirect_response=False)

    def test_seleccion_rol_renderiza(self):
        self.assertEqual(self.client.get(reverse('seleccion_rol')).status_code, 200)


# ============================================================
# 6. Control de acceso por rol (decorador rol_requerido) — 1ª sesión
# ============================================================
class ControlDeAccesoTests(TestCase):
    def setUp(self):
        self.farma = _crear_usuario(rol='farmacia', email='farma@x.com', cedula='111')
        self.lab = _crear_usuario(rol='laboratorio', email='lab@x.com', cedula='222')
        self.url = reverse('dashboard_farmacia')

    def test_sin_sesion_redirige_al_login(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/login', resp.url)

    def test_rol_correcto_entra(self):
        self.client.login(email='farma@x.com', password='ClaveSegura123')
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_rol_incorrecto_recibe_403(self):
        self.client.login(email='lab@x.com', password='ClaveSegura123')
        self.assertEqual(self.client.get(self.url).status_code, 403)


# ============================================================
# 7. Modelo Usuario (incluye cambio de rol y unicidad)
# ============================================================
class UsuarioModeloTests(TestCase):
    def test_str_incluye_nombre_y_rol(self):
        u = _crear_usuario(rol='medico', email='m@x.com')
        u.first_name, u.last_name = 'Greg', 'House'
        u.save()
        self.assertIn('Greg House', str(u))
        self.assertIn('Médico', str(u))

    def test_cambiar_rol_persiste(self):
        u = _crear_usuario(rol='farmacia', email='cambio@x.com')
        u.rol = 'admin'
        u.save()
        u.refresh_from_db()
        self.assertEqual(u.rol, 'admin')

    def test_email_es_unico(self):
        from django.db import IntegrityError, transaction
        _crear_usuario(email='dup@x.com', cedula='900')
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                _crear_usuario(email='dup@x.com', cedula='901')

    def test_login_es_por_email(self):
        self.assertEqual(Usuario.USERNAME_FIELD, 'email')
