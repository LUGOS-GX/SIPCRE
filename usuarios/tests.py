"""
Tests del módulo usuarios: control de acceso por rol (decorador rol_requerido).
Estos son tests de VISTA: usan el cliente de pruebas para simular peticiones
HTTP reales y verificar quién puede entrar a cada zona del sistema.
"""
from django.test import TestCase
from django.urls import reverse

from usuarios.models import Usuario


class ControlDeAccesoPorRolTests(TestCase):
    def setUp(self):
        # Un usuario de farmacia y uno de laboratorio para probar fronteras.
        self.farmaceuta = Usuario.objects.create_user(
            username='farma', email='farma@cruzroja.org', password='clave12345',
            cedula='111', rol='farmacia', telefono='0412',
        )
        self.laboratorista = Usuario.objects.create_user(
            username='lab', email='lab@cruzroja.org', password='clave12345',
            cedula='222', rol='laboratorio', telefono='0412',
        )
        self.url_farmacia = reverse('dashboard_farmacia')

    def test_visitante_sin_sesion_es_redirigido_al_login(self):
        respuesta = self.client.get(self.url_farmacia)
        self.assertEqual(respuesta.status_code, 302)
        self.assertIn('/login', respuesta.url)

    def test_rol_correcto_entra(self):
        self.client.login(email='farma@cruzroja.org', password='clave12345')
        respuesta = self.client.get(self.url_farmacia)
        self.assertEqual(respuesta.status_code, 200)

    def test_rol_incorrecto_recibe_403(self):
        self.client.login(email='lab@cruzroja.org', password='clave12345')
        respuesta = self.client.get(self.url_farmacia)
        self.assertEqual(respuesta.status_code, 403)


class UsuarioModeloTests(TestCase):
    def test_str_incluye_nombre_y_rol(self):
        u = Usuario.objects.create_user(
            username='m', email='m@cruzroja.org', password='x',
            cedula='333', rol='medico', telefono='0412',
            first_name='Gregory', last_name='House',
        )
        self.assertIn('Gregory House', str(u))
        self.assertIn('Médico', str(u))
