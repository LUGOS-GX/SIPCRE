from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponse
from django.core.mail import send_mail
from django.conf import settings
from django.db.models import Q, Sum, Count
from django.db.models.functions import TruncDate
from django.views.decorators.http import require_POST
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.utils import timezone
from datetime import timedelta
from .models import Paciente, Cita, Medico, Factura, DetalleFactura, SesionCaja, CatalogoServicio, PagoFactura
from medico.models import ConsultaEvolucion
from farmacia.models import MovimientoInventario
from laboratorio.models import ResultadoDetalle, SolicitudExamen
from usuarios.decorators import rol_requerido
from usuarios.models import Usuario
from datetime import datetime
from decimal import Decimal
from .utils import obtener_tasa_bcv
from collections import Counter
import json
import traceback
import openpyxl
from openpyxl.chart import BarChart, LineChart, PieChart, Reference

@login_required
@rol_requerido(['admin'])
def dashboard_admin(request):
    # 1. Obtenemos fecha actual Vzla por defecto
    hoy = timezone.localtime(timezone.now()).date()
    fecha_filtro_str = request.GET.get('fecha')

    if fecha_filtro_str:
        try:
            # FIX: Convertimos a objeto Date estricto para evitar fallos del motor de DB
            fecha_filtro = datetime.strptime(fecha_filtro_str, '%Y-%m-%d').date()
        except ValueError:
            fecha_filtro = hoy
    else:
        fecha_filtro = hoy

    # 3. Consulta Base (Aún mostramos todas, en la Fase 2 sacaremos las "Atendidas" de aquí)
    citas_list = Cita.objects.filter(fecha=fecha_filtro).order_by('hora')
    
    # NUEVO: Paginación (10 citas por página para no saturar el DOM)
    paginator = Paginator(citas_list, 5)
    page_number = request.GET.get('page')
    citas = paginator.get_page(page_number)

    context = {
        'citas': citas,
        'fecha_actual_str': fecha_filtro.strftime('%Y-%m-%d'), # Para el input tipo date
        'hoy': hoy
    }
    return render(request, 'administracion/dashboard.html', context)

@login_required
@rol_requerido(['admin'])
def agendar_cita(request):
    if request.method == 'POST':
        # 1. Recuperar datos del nuevo formulario
        cedula = request.POST.get('cedula').strip()
        nombre = request.POST.get('nombre_nuevo').strip()
        email = request.POST.get('email', '').strip()
        tipo_sangre = request.POST.get('tipo_sangre', '').strip()
        telefono = request.POST.get('telefono', '').strip()
        fecha_nacimiento = request.POST.get('fecha_nacimiento', '').strip()
        tiene_seguro = request.POST.get('tiene_seguro') == 'on'
        nombre_seguro = request.POST.get('nombre_seguro', '').strip()
        
        medico_id = request.POST.get('medico_id')
        fecha = request.POST.get('fecha')
        hora = request.POST.get('hora')
        servicios_ids = request.POST.getlist('servicios[]')

        # Manejar fecha vacía para evitar errores en la base de datos (Date no acepta strings vacíos)
        fecha_nac = fecha_nacimiento if fecha_nacimiento else None

        # 2. Búsqueda Inteligente: Si el paciente no existe, lo crea automáticamente
        paciente, created = Paciente.objects.get_or_create(
            cedula=cedula,
            defaults={
                'nombres': nombre,
                'email': email,
                'tipo_sangre': tipo_sangre,
                'telefono': telefono,
                'fecha_nacimiento': fecha_nac,
                'tiene_seguro': tiene_seguro,
                'nombre_seguro': nombre_seguro if tiene_seguro else '',
                'nacionalidad': 'V' 
            }
        )
        
        # 3. Si ya existía, actualizamos sus datos si la recepcionista los llenó o modificó
        if not created:
            if email: paciente.email = email
            if tipo_sangre: paciente.tipo_sangre = tipo_sangre
            if telefono: paciente.telefono = telefono
            if fecha_nac: paciente.fecha_nacimiento = fecha_nac
            paciente.tiene_seguro = tiene_seguro
            paciente.nombre_seguro = nombre_seguro if tiene_seguro else ''
            paciente.save()

        # 4. Crear la Cita
        cita = Cita.objects.create(
            paciente=paciente,
            medico_id=medico_id,
            fecha=fecha,
            hora=hora,
            motivo=request.POST.get('motivo'),
            estado='Pendiente'
        )

        # 5. Redirección a caja con los servicios (Carrito Express)
        if 'pagar_ahora' in request.POST:
            request.session['carrito_express'] = {
                'paciente_cedula': paciente.cedula,
                'servicios_ids': servicios_ids,
                'cita_id': cita.id
            }
            messages.success(request, "Cita agendada. Redirigiendo a caja...")
            return redirect('caja_central')
        
        messages.success(request, "Cita agendada exitosamente.")
        return redirect('dashboard_admin')

    # GET: Cargar datos para el frontend (Añadimos telefono y fecha_nacimiento a la consulta)
    catalogo = list(CatalogoServicio.objects.filter(activo=True).values('id', 'nombre', 'precio_usd'))
    pacientes = list(Paciente.objects.all().values('id', 'cedula', 'nombres', 'email', 'tipo_sangre', 'telefono', 'fecha_nacimiento'))
    
    context = {
        'medicos': Medico.objects.all(),
        'fecha_actual': timezone.now().date().strftime('%Y-%m-%d'),
        'catalogo_json': json.dumps(catalogo, default=str),
        'pacientes_json': json.dumps(pacientes, default=str)
    }
    return render(request, 'administracion/agendar_cita.html', context)

@login_required
@rol_requerido(['admin'])
def registrar_orden_externa(request):
    # Listas de exámenes (Mismas que el módulo médico)
    examenes_lab = [
        'Hematología', 'Glicemia', 'Urea', 'Creatinina', 'Ácido Úrico', 
        'Colesterol', 'Triglicéridos', 'Perfil Lipídico', 'PT', 'PTT', 
        'Fibrinógeno', 'HIV', 'VDRL', 'VSG', 'HCG cualitativa', 'PCR', 
        'Proteína T y F', 'Calcio', 'Fósforo', 'Mágnesio', 'TGO - TGP', 
        'Bilirrubina', 'Fosfatasa alcalina', 'Drogas de abuso', 'Heces', 'Orina'
    ]
    examenes_img = ['Rayos X', 'Ecosonograma']

    if request.method == 'POST':
        # 1. Capturamos Datos del Paciente (Walk-in)
        nombre = request.POST.get('nombre')
        cedula = f"{request.POST.get('nacionalidad')}-{request.POST.get('cedula')}"
        
        # 2. Capturamos los exámenes seleccionados
        seleccionados = request.POST.getlist('examenes')
        otros = request.POST.get('otros_detalle')
        
        print("--- ORDEN EXTERNA CREADA (ADMIN) ---")
        print(f"Paciente: {nombre} | CI: {cedula}")
        print(f"Solicitud: {seleccionados}")
        if otros: print(f"Otros: {otros}")
        
        # Redirigimos al dashboard administrativo (o se podría imprimir factura)
        return redirect('dashboard_admin')

    context = {
        'examenes_lab': examenes_lab,
        'examenes_img': examenes_img
    }
    return render(request, 'administracion/orden_externa.html', context)

@login_required
@rol_requerido(['admin'])
def editar_cita(request, id_cita):
    cita = get_object_or_404(Cita, id=id_cita)
    paciente = cita.paciente

    if request.method == 'POST':
        # Actualizar Datos del Paciente (Incluyendo Cédula)
        paciente.cedula = request.POST.get('cedula', paciente.cedula).strip()
        paciente.nombres = request.POST.get('nombre_nuevo', paciente.nombres).strip()
        paciente.email = request.POST.get('email', '').strip()
        paciente.telefono = request.POST.get('telefono', '').strip()
        paciente.tipo_sangre = request.POST.get('tipo_sangre', '').strip()
        
        f_nac = request.POST.get('fecha_nacimiento', '').strip()
        paciente.fecha_nacimiento = f_nac if f_nac else None
        
        tiene_seguro = request.POST.get('tiene_seguro') == 'on'
        paciente.tiene_seguro = tiene_seguro
        paciente.nombre_seguro = request.POST.get('nombre_seguro', '').strip() if tiene_seguro else ''
        
        try:
            paciente.save()
        except Exception as e:
            messages.error(request, "Error al actualizar los datos del paciente: La cédula podría ya estar registrada.")
            return redirect('editar_cita', id_cita=id_cita)

        # Actualizar Datos de la Cita
        medico_id = request.POST.get('medico_id')
        if medico_id:
            cita.medico_id = medico_id
            
        cita.fecha = request.POST.get('fecha')
        cita.hora = request.POST.get('hora')
        cita.motivo = request.POST.get('motivo')
        cita.estado = request.POST.get('estado')
        
        cita.save()

        messages.success(request, f"La cita #{cita.id} de {paciente.nombres} ha sido actualizada correctamente.")
        return redirect(f'/administracion/?fecha={cita.fecha}')

    tipos_sangre = ["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"]

    context = {
        'cita': cita,
        'paciente': paciente,
        'medicos': Medico.objects.all(),
        'tipos_sangre': tipos_sangre,
        'fecha_actual': timezone.now().date().strftime('%Y-%m-%d'),
    }
    return render(request, 'administracion/editar_cita.html', context)

@login_required
@rol_requerido(['admin'])
def caja_central(request):
    # 1. GESTIÓN DE SESIÓN Y TASA BCV
    sesion = SesionCaja.objects.filter(cajero=request.user, estado='Abierta').first()
    hoy = timezone.localtime(timezone.now()).date()
    
    if sesion:
        # PARCHE ANTI-OLVIDOS: Si la caja quedó abierta de ayer, actualizamos la tasa al día de hoy
        if sesion.fecha_apertura.date() != hoy:
            nueva_tasa = obtener_tasa_bcv()
            if nueva_tasa:
                sesion.tasa_bcv_dia = nueva_tasa
                sesion.save()
    else:
        # Si no hay sesión abierta, la creamos y consultamos la API
        tasa_actual = obtener_tasa_bcv() 
        if not tasa_actual:
            tasa_actual = Decimal('500.00') 
            messages.warning(request, "No se pudo conectar al BCV. Se aplicó tasa manual de respaldo.")
        
        sesion = SesionCaja.objects.create(
            cajero=request.user,
            tasa_bcv_dia=tasa_actual,
            estado='Abierta'
        )

    # 2. PROCESAMIENTO DEL PAGO (Vía AJAX)
    if request.method == 'POST' and request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        try:
            data = json.loads(request.body)
            cedula = data.get('cedula')
            nombre = data.get('nombre')
            carrito = data.get('carrito', [])
            pagos = data.get('pagos', [])
            facturas_pendientes_ids = data.get('facturas_pendientes', [])
            
            # 1. Crear o recuperar paciente de emergencia
            paciente, created = Paciente.objects.get_or_create(
                cedula=cedula, 
                defaults={'nombres': nombre, 'nacionalidad': 'V'}
            )
            
            # 2. Marcar las facturas viejas (Farmacia/Médico) como "Pagadas"
            if facturas_pendientes_ids:
                Factura.objects.filter(id__in=facturas_pendientes_ids).update(
                    estado='Pagada',
                    paciente=paciente, 
                    nombre_cliente=nombre,
                    cedula_cliente=cedula
                )
            
            # 3. Filtrar si se agregaron servicios NUEVOS desde el catálogo de la Caja
            items_nuevos = [item for item in carrito if not str(item.get('id', '')).startswith('pendiente_')]
            
            factura_maestra = None
            if items_nuevos:
                total_nuevos = sum(Decimal(str(item['precio'])) * int(item['cantidad']) for item in items_nuevos)
                factura_maestra = Factura.objects.create(
                    paciente=paciente, 
                    nombre_cliente=nombre,
                    cedula_cliente=cedula,
                    total=total_nuevos,
                    estado='Pagada'
                )
                for item in items_nuevos:
                    precio_u = Decimal(str(item['precio']))
                    DetalleFactura.objects.create(
                        factura=factura_maestra,
                        descripcion=item['nombre'],
                        cantidad=int(item['cantidad']),
                        precio_unitario=precio_u,
                        subtotal=precio_u * int(item['cantidad'])
                    )
                    
            # 4. Registrar los pagos en la factura generada
            factura_id_destino = factura_maestra.id if factura_maestra else facturas_pendientes_ids[0]
            factura_destino = Factura.objects.get(id=factura_id_destino)
            
            for p in pagos:
                PagoFactura.objects.create(
                    factura=factura_destino,
                    metodo=p['metodo'],
                    monto_moneda_original=Decimal(str(p['monto_ingresado'])),
                    monto_equivalente_usd=Decimal(str(p['equivalente_usd'])),
                    referencia=p.get('referencia', '')
                )
            
            return JsonResponse({'status': 'success', 'factura_id': factura_destino.id})
            
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)})

    # Sacamos los datos de la sesión (usamos pop para que se limpie tras el primer uso)
    carrito_express = request.session.pop('carrito_express', None)
    paciente_express = None
    servicios_express = []

    if carrito_express:
        cedula_ex = carrito_express.get('paciente_cedula')
        paciente_express = Paciente.objects.filter(cedula=cedula_ex).first()
        servicios_ids = carrito_express.get('servicios_ids', [])
        
        # Obtenemos los datos mínimos necesarios de los servicios para el JS
        servicios_express = list(CatalogoServicio.objects.filter(id__in=servicios_ids).values('id', 'nombre', 'precio_usd'))

    # 3. GET: ENVIAR DATOS AL FRONTEND
    catalogo = list(CatalogoServicio.objects.filter(activo=True).values('id', 'nombre', 'categoria', 'precio_usd'))
    pacientes = list(Paciente.objects.all().values('cedula', 'nombres'))
    
    context = {
        'sesion': sesion,
        'tasa_bcv': float(sesion.tasa_bcv_dia),
        'catalogo_json': json.dumps(catalogo, default=str),
        'pacientes_json': json.dumps(pacientes),
        'paciente_express': paciente_express,
        'servicios_express': json.dumps(servicios_express, default=str)
    }
    return render(request, 'administracion/caja_central.html', context)

@login_required
@rol_requerido(['admin']) 
def sala_espera(request):
    # Traemos solo a los usuarios que están "congelados"
    usuarios_pendientes = Usuario.objects.filter(is_active=False).order_by('-date_joined')
    return render(request, 'administracion/sala_espera.html', {'usuarios_pendientes': usuarios_pendientes})

@login_required
@rol_requerido(['admin'])
def aprobar_usuario(request, usuario_id):
    usuario = get_object_or_404(Usuario, id=usuario_id)
    usuario.is_active = True  # ¡Descongelado!
    usuario.save()
    
    # Lógica de envío de correo
    try:
        nombre_usuario = f"{usuario.first_name} {usuario.last_name}".strip()
        if not nombre_usuario:
            nombre_usuario = "Usuario" # Respaldo por si acaso
            
        asunto = '¡Cuenta Aprobada en SIPCRE!'
        mensaje = f'Hola {nombre_usuario},\n\nTu cuenta en el Sistema Integral Para Cruz Roja Especializado (SIPCRE) ha sido verificada y aprobada por Recursos Humanos.\n\nYa puedes iniciar sesión en el sistema.\n\nSaludos,\nEl equipo de SIPCRE.'
        
        send_mail(asunto, mensaje, settings.DEFAULT_FROM_EMAIL, [usuario.email], fail_silently=False)
        messages.success(request, f'El usuario {nombre_usuario} ha sido aprobado y notificado por correo.')
    except Exception as e:
        messages.warning(request, f'Usuario aprobado, pero ocurrió un error al enviar el correo: {e}')
        
    return redirect('sala_espera')

@login_required
@rol_requerido(['admin'])
def rechazar_usuario(request, usuario_id):
    usuario = get_object_or_404(Usuario, id=usuario_id)
    nombre = f"{usuario.first_name} {usuario.last_name}".strip()
    usuario.delete() 
    messages.success(request, f'La solicitud de {nombre} ha sido rechazada y eliminada del sistema.')
    return redirect('sala_espera')

@login_required
@rol_requerido(['admin'])
def historial_citas(request):
    """ Vista para ver únicamente las citas que ya fueron atendidas """
    fecha_filtro_str = request.GET.get('fecha')

    # Consulta base: SOLO citas en estado 'Atendido'
    citas_query = Cita.objects.filter(estado='Atendido').order_by('-fecha', '-hora')

    if fecha_filtro_str:
        try:
            fecha_filtro = datetime.strptime(fecha_filtro_str, '%Y-%m-%d').date()
            citas_query = citas_query.filter(fecha=fecha_filtro)
        except ValueError:
            pass # Si la fecha es inválida, mostramos todo el historial

    # Paginación (15 registros por página para el historial)
    paginator = Paginator(citas_query, 10)
    page_number = request.GET.get('page')
    citas_atendidas = paginator.get_page(page_number)

    context = {
        'citas': citas_atendidas,
        'fecha_actual_str': fecha_filtro_str if fecha_filtro_str else '', 
    }
    return render(request, 'administracion/historial_citas.html', context)

@login_required
@rol_requerido(['admin'])
@require_POST
def eliminar_cita(request, cita_id):
    """ Función segura para eliminar una cita (Pendiente o Atendida) """
    cita = get_object_or_404(Cita, id=cita_id)
    
    # Guardamos los datos para el mensaje de éxito antes de borrarla
    info_cita = f"{cita.paciente.nombres} ({cita.fecha.strftime('%d/%m/%Y')})"
    
    try:
        cita.delete()
        messages.success(request, f"La cita de {info_cita} ha sido eliminada del sistema.")
    except Exception as e:
        messages.error(request, f"Error al intentar eliminar la cita: {str(e)}")
        
    # Redirigir de vuelta a la página desde donde se hizo la petición (Dashboard o Historial)
    url_origen = request.META.get('HTTP_REFERER', 'dashboard_admin')
    return redirect(url_origen)

@login_required
@rol_requerido(['admin'])
def lista_personal(request):
    query = request.GET.get('q', '').strip()
    usuarios = Usuario.objects.all().order_by('last_name')

    # Aplicar búsqueda si existe
    if query:
        usuarios = usuarios.filter(
            Q(first_name__icontains=query) |
            Q(last_name__icontains=query) |
            Q(cedula__icontains=query) |
            Q(email__icontains=query)
        )

    # Función auxiliar para paginar cada grupo
    def paginar_grupo(queryset):
        paginator = Paginator(queryset, 10) # 10 por página
        page_number = request.GET.get('page')
        return paginator.get_page(page_number)

    personal = {
        'medicos': paginar_grupo(usuarios.filter(rol='medico')),
        'farmacia': paginar_grupo(usuarios.filter(rol='farmacia')),
        'laboratorio': paginar_grupo(usuarios.filter(rol='laboratorio')),
        'administracion': paginar_grupo(usuarios.filter(rol='admin')),
    }
    
    return render(request, 'administracion/lista_personal.html', {
        'personal': personal,
        'query': query
    })

@login_required
@rol_requerido(['admin'])
def obtener_deudas_paciente(request, cedula):
    """ Busca si el paciente tiene facturas pendientes en Farmacia, Lab o Médico """
    facturas_pendientes = Factura.objects.filter(cedula_cliente=cedula, estado='Pendiente')
    
    deudas = []
    facturas_ids = []
    
    for fac in facturas_pendientes:
        facturas_ids.append(fac.id)
        for det in fac.detalles.all(): 
            deudas.append({
                'id': f"pendiente_{det.id}", # Un ID único para que el carrito no se confunda
                'nombre': f"🟡 [PENDIENTE] {det.descripcion}",
                'precio': float(det.precio_unitario),
                'cantidad': det.cantidad,
                'factura_id': fac.id # Clave para saber que esto ya venía de otra área
            })
            
    return JsonResponse({'status': 'success', 'deudas': deudas, 'facturas_ids': facturas_ids})

@login_required
@rol_requerido(['admin'])
def cerrar_caja(request):
    """ Calcula los totales de la sesión actual, la cierra y redirige al reporte """
    sesion = SesionCaja.objects.filter(cajero=request.user, estado='Abierta').first()
    
    if not sesion:
        messages.error(request, "No tiene una caja abierta actualmente.")
        return redirect('dashboard_admin')
        
    # Obtener todos los pagos registrados desde que se abrió esta caja
    pagos = PagoFactura.objects.filter(fecha_pago__gte=sesion.fecha_apertura)
    
    # Función auxiliar para sumar montos por método de pago
    def suma_metodo(metodo):
        total = pagos.filter(metodo=metodo).aggregate(Sum('monto_moneda_original'))['monto_moneda_original__sum']
        return total if total else 0
        
    # Guardamos los totales en el modelo de la Sesión
    sesion.total_usd_efectivo = suma_metodo('Efectivo USD')
    sesion.total_zelle = suma_metodo('Zelle')
    sesion.total_bs_efectivo = suma_metodo('Efectivo Bs')
    sesion.total_pago_movil = suma_metodo('Pago Movil')
    sesion.total_punto_venta = suma_metodo('Punto de Venta')
    
    sesion.fecha_cierre = timezone.now()
    sesion.estado = 'Cerrada'
    sesion.save()
    
    messages.success(request, f"Caja cerrada exitosamente. Arqueo generado.")
    return redirect('imprimir_cierre', sesion_id=sesion.id)

@login_required
@rol_requerido(['admin'])
def imprimir_cierre(request, sesion_id):
    """ Genera la vista limpia para el reporte en PDF """
    sesion = get_object_or_404(SesionCaja, id=sesion_id)
    
    # Cálculos globales para el reporte
    total_bs = sesion.total_bs_efectivo + sesion.total_pago_movil + sesion.total_punto_venta
    total_usd_puro = sesion.total_usd_efectivo + sesion.total_zelle
    
    # Convertimos los bolívares a dólares usando la tasa del día en que se abrió la caja
    gran_total_usd = float(total_usd_puro) + (float(total_bs) / float(sesion.tasa_bcv_dia))
    
    context = {
        'sesion': sesion,
        'total_bs': total_bs,
        'total_usd_puro': total_usd_puro,
        'gran_total_usd': gran_total_usd
    }
    # Usaremos un layout sin menú lateral para que se imprima perfecto
    return render(request, 'administracion/pdf_cierre_caja.html', context)

@login_required
@rol_requerido(['admin'])
def historico_caja(request):
    """ Vista para consultar todas las transacciones procesadas por la caja """
    query = request.GET.get('q', '').strip()
    fecha_filtro = request.GET.get('fecha', '')

    # Traemos todas las facturas y pre-cargamos sus detalles y pagos para optimizar
    facturas = Factura.objects.prefetch_related('detalles', 'pagos').order_by('-id')

    # Filtro por Fecha
    if fecha_filtro:
        # Asumiendo que tu Factura tiene un campo de fecha o timestamp
        # Si el campo se llama de otra forma en tu BD (ej. fecha_emision), ajústalo aquí:
        facturas = facturas.filter(fecha_emision__date=fecha_filtro)

    # Filtro por Búsqueda Inteligente (Cédula o Nombre)
    if query:
        facturas = facturas.filter(
            Q(nombre_cliente__icontains=query) |
            Q(cedula_cliente__icontains=query) |
            Q(paciente__nombres__icontains=query) |
            Q(paciente__cedula__icontains=query) |
            Q(id__icontains=query) 
        )

    # Paginación: 15 facturas por página
    paginator = Paginator(facturas, 15)
    page_number = request.GET.get('page')
    facturas_paginadas = paginator.get_page(page_number)

    context = {
        'facturas': facturas_paginadas,
        'query': query,
        'fecha_filtro': fecha_filtro
    }
    return render(request, 'administracion/historico_caja.html', context)

@login_required
@rol_requerido(['admin'])
def datos_estadisticas(request):
    try:
        periodo = request.GET.get('periodo', 'mes')
        ahora = timezone.now()
        
        if periodo == 'semana':
            inicio = ahora - timedelta(days=7)
        elif periodo == 'ano':
            inicio = ahora - timedelta(days=365)
        else:
            inicio = ahora - timedelta(days=30)

        # 1. MORBILIDAD
        morbilidad = list(ConsultaEvolucion.objects.filter(fecha__gte=inicio)
                          .exclude(diagnostico__isnull=True).exclude(diagnostico='')
                          .values('diagnostico')
                          .annotate(total=Count('id'))
                          .order_by('-total')[:5])
        for i in morbilidad: i['motivo'] = i.pop('diagnostico')

        # 2. FLUJO DE PACIENTES 
        flujo_data = list(ConsultaEvolucion.objects.filter(fecha__gte=inicio)
                     .annotate(fecha_dia=TruncDate('fecha'))
                     .values('fecha_dia')
                     .annotate(total=Count('id'))
                     .order_by('fecha_dia'))
        
        flujo = []
        for i in flujo_data:
            f_dia = i.get('fecha_dia')
            flujo.append({
                'fecha': f_dia.strftime('%d/%m/%Y') if hasattr(f_dia, 'strftime') else str(f_dia),
                'total': i['total']
            })

        # 3. MEDICAMENTOS
        medicamentos_data = list(MovimientoInventario.objects.filter(tipo_movimiento='SALIDA', fecha__gte=inicio)
                            .values('medicamento__nombre')
                            .annotate(total=Sum('cantidad'))
                            .order_by('total')[:5])
        medicamentos = []
        for i in medicamentos_data: 
            medicamentos.append({
                'descripcion': i['medicamento__nombre'],
                'total': abs(i['total']) if i['total'] else 0
            })

        # 4. EXÁMENES (Lógica exacta del laboratorio)
        ordenes = SolicitudExamen.objects.filter(fecha_solicitud__gte=inicio)
        todos_examenes_lista = []
        for orden in ordenes:
            if orden.examenes_solicitados:
                # Usamos una variable temporal distinta para no pisar el resultado final
                lista_temp = [e.strip() for e in orden.examenes_solicitados.split(',') if e.strip()]
                todos_examenes_lista.extend(lista_temp)

        contador = Counter(todos_examenes_lista)
        top_diez = contador.most_common(10)

        # Formateamos para el .map() de tu JS en el dashboard
        examenes_final = []
        for item in top_diez:
            examenes_final.append({
                'descripcion': item[0],
                'total': item[1]
            })

        return JsonResponse({
            'morbilidad': morbilidad,
            'flujo': flujo,
            'medicamentos': medicamentos,
            'examenes': examenes_final 
        })

    except Exception as e:
        print("ERROR EN ESTADISTICAS:", traceback.format_exc())
        return JsonResponse({'error': str(e)}, status=500)

@login_required
@rol_requerido(['admin'])
def exportar_excel_estadisticas(request, tipo):
    """ Genera un Excel con los datos y DIBUJA un gráfico nativo dentro de Excel """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = f"Reporte {tipo.capitalize()}"

    # Preparamos las columnas
    ws.append(["Categoría", "Cantidad"])

    # Obtenemos los datos reutilizando la vista anterior (simulando una petición interna)
    datos = json.loads(datos_estadisticas(request).content)[tipo]

    # Llenamos las celdas
    for fila in datos:
        etiqueta = fila.get('motivo') or fila.get('fecha') or fila.get('descripcion') or 'Sin nombre'
        ws.append([str(etiqueta), int(fila['total'])])

    # --- MAGIA: DIBUJAR GRÁFICO EN EXCEL ---
    if tipo == 'flujo':
        chart = LineChart()
        chart.title = "Flujo de Pacientes en el Tiempo"
    elif tipo == 'morbilidad':
        chart = PieChart()
        chart.title = "Índice de Morbilidad"
    else:
        chart = BarChart()
        chart.title = f"Top {tipo.capitalize()}"

    # Referenciar los datos insertados (desde fila 2 hasta el final)
    data_ref = Reference(ws, min_col=2, min_row=1, max_row=len(datos)+1)
    cats_ref = Reference(ws, min_col=1, min_row=2, max_row=len(datos)+1)
    
    chart.add_data(data_ref, titles_from_data=True)
    chart.set_categories(cats_ref)
    
    # Insertar el gráfico en la celda E2
    ws.add_chart(chart, "E2")

    # Preparar respuesta de descarga
    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename=SIPCRE_{tipo}.xlsx'
    wb.save(response)
    return response

@login_required
@rol_requerido(['admin'])
def pdf_estadisticas(request):
    """ Muestra la plantilla a pantalla completa lista para imprimir como PDF """
    # Reutilizamos los datos para que Jinja los pinte en el HTML
    context = json.loads(datos_estadisticas(request).content)
    context['fecha_hoy'] = timezone.now().strftime("%d/%m/%Y")
    return render(request, 'administracion/pdf_estadisticas.html', context)

@login_required
@rol_requerido(['admin'])
def editar_perfil_admin(request):
    usuario = request.user
    
    if request.method == 'POST':
        try:
            # 1. Procesar Nombre y Apellido
            nombre_completo = request.POST.get('nombre_completo', '').strip()
            if nombre_completo:
                partes = nombre_completo.split(' ', 1)
                usuario.first_name = partes[0]
                usuario.last_name = partes[1] if len(partes) > 1 else ''

            # 2. Procesar Datos de Contacto
            usuario.cedula = request.POST.get('cedula', usuario.cedula)
            usuario.telefono = request.POST.get('telefono', usuario.telefono)
            usuario.email = request.POST.get('email', usuario.email)

            # 3. Procesar Foto de Perfil
            if 'foto_perfil' in request.FILES:
                usuario.foto_perfil = request.FILES['foto_perfil']

            usuario.save()
            messages.success(request, "Tu perfil administrativo ha sido actualizado exitosamente.")
            return redirect('editar_perfil_admin')

        except Exception as e:
            messages.error(request, f"Hubo un error al actualizar el perfil: {e}")
            return redirect('editar_perfil_admin')

    return render(request, 'administracion/editar_perfil.html', {'usuario': usuario})

@login_required
@rol_requerido(['admin'])
def verificar_disponibilidad(request):
    medico_id = request.GET.get('medico_id')
    fecha = request.GET.get('fecha')
    
    # Buscamos las horas ya ocupadas para ese médico en ese día
    horas_ocupadas = list(Cita.objects.filter(
        medico_id=medico_id, 
        fecha=fecha, 
        estado__in=['Pendiente', 'En Sala', 'Atendido']
    ).values_list('hora', flat=True))
    
    # Formateamos las horas (HH:MM) para que JS las reconozca
    horas_formateadas = [h.strftime('%H:%M') for h in horas_ocupadas]
    
    return JsonResponse({'ocupadas': horas_formateadas})
