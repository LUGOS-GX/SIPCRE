# SIPCRE
Sistema de Información de la Cruz Roja (SOLO CON FINES EDUCATIVOS)

SIPCRE es un sistema integral desarrollado para la gestion de procesos clinicos y administrativos en el centro de salud de la Cruz Roja, seccional Barcelona. El proyecto esta estructurado para optimizar la atencion al paciente, el control de inventarios medicos y el flujo de informacion entre los distintos departamentos del recinto.

## Modulos Principales

El sistema esta compuesto por cinco aplicaciones principales integradas entre si:

* Administracion: Gestión de citas, sala de espera, ordenes externas, facturacion y control de caja central.
* Medico: Manejo de historias clínicas, evolucion de pacientes, emision de recipes, constancias medicas y solicitudes de examenes.
* Laboratorio: Recepcion de solicitudes, procesamiento de resultados, generacion de reportes en PDF y notificaciones por correo a los pacientes.
* Farmacia: Control de inventario general, gestion de lotes, kardex, despachos, ajustes de inventario y caja de farmacia.
* Usuarios: Sistema de autenticación, roles de acceso y perfiles especializados para medicos y personal administrativo.

## Tecnologias Utilizadas (Stack)

El proyecto esta desarrollado bajo una arquitectura monolítica utilizando el framework Django:

* Backend: Python, Django.
* Frontend: HTML5, CSS3, JavaScript (Vanilla), sistema de plantillas de Django (Django Templates).
* Base de Datos: Postgres
* Generacion de Documentos: Herramientas para exportacion de historias y resultados a formato PDF.

Sistema hecho como requisito para optar al título de ing. de sistemas. 
