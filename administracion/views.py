from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponse, Http404
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
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from django.db import transaction
from django.core.exceptions import ValidationError
from .utils import obtener_tasa_bcv
from core.correo_utils import enviar_comprobante_pago, correo_es_valido, _METODOS_USD
from core.validators import (
    normalizar_cedula, cedula_es_valida, validar_imagen,
    normalizar_nombre, nombre_es_valido,
)
from collections import Counter
import json
import logging
import openpyxl
from openpyxl.chart import BarChart, LineChart, PieChart, Reference

logger = logging.getLogger('sipcre')

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

    citas_list = Cita.objects.filter(fecha=fecha_filtro).exclude(estado='Atendido').select_related('paciente', 'medico').order_by('hora')
    
    #Paginacion
    paginator = Paginator(citas_list, 5)
    page_number = request.GET.get('page')
    citas = paginator.get_page(page_number)

    context = {
        'citas': citas,
        'fecha_actual_str': fecha_filtro.strftime('%Y-%m-%d'), 
        'fecha_display': fecha_filtro.strftime('%d/%m/%Y'),      
        'hoy': hoy
    }
    return render(request, 'administracion/dashboard.html', context)

@login_required
@rol_requerido(['admin'])
def agendar_cita(request):
    if request.method == 'POST':
        # 1. Recuperar datos del nuevo formulario
        cedula = normalizar_cedula(request.POST.get('cedula'))
        if not cedula_es_valida(cedula):
            messages.error(request, "La cédula no es válida: debe ser numérica y no superar los 40.000.000.")
            return redirect('agendar_cita')
        nombre = normalizar_nombre(request.POST.get('nombre_nuevo'))
        email = (request.POST.get('email') or '').strip()
        tipo_sangre = (request.POST.get('tipo_sangre') or '').strip()
        telefono = (request.POST.get('telefono') or '').strip()
        fecha_nacimiento = (request.POST.get('fecha_nacimiento') or '').strip()
        tiene_seguro = request.POST.get('tiene_seguro') == 'on'
        nombre_seguro = (request.POST.get('nombre_seguro') or '').strip()

        # ¿Esta cita se agendó como CONTROL de un paciente ya registrado?
        # (lo marca el botón "Control / Px registrado" del formulario)
        es_control = request.POST.get('es_control') == '1'

        medico_id = request.POST.get('medico_id')
        fecha = request.POST.get('fecha')
        hora = request.POST.get('hora')
        servicios_ids = request.POST.getlist('servicios[]')

        # Campos obligatorios de la cita: sin esto, el create() de abajo
        # revienta con un 500 en vez de un mensaje claro.
        if not nombre:
            messages.error(request, "Debe indicar el nombre del paciente.")
            return redirect('agendar_cita')
        if not nombre_es_valido(nombre):
            messages.error(request, "El nombre no es válido: 2 a 60 caracteres, solo letras y espacios.")
            return redirect('agendar_cita')
        if not (medico_id and fecha and hora):
            messages.error(request, "Debe seleccionar médico, fecha y hora para la cita.")
            return redirect('agendar_cita')

        # La cédula es un documento único e irrepetible. En modo NORMAL (cita
        # nueva), si ya existe un paciente con esa cédula NO se crea un
        # duplicado ni se actualiza a ciegas: se bloquea y se indica usar el
        # botón de control. En modo CONTROL sí se espera un paciente existente.
        paciente_existente = Paciente.objects.filter(cedula=cedula).first()
        if paciente_existente and not es_control:
            messages.error(request, "Esta cédula ya está registrada. Use el botón \"Control / Px registrado\" para agendarle una cita de control.")
            return redirect('agendar_cita')
        if es_control and not paciente_existente:
            messages.error(request, "No se encontró un paciente registrado con esa cédula. Verifique o agéndelo como cita nueva.")
            return redirect('agendar_cita')

        # Manejar fecha vacía para evitar errores en la base de datos (Date no acepta strings vacíos)
        fecha_nac = fecha_nacimiento if fecha_nacimiento else None

        # Paciente y cita se crean juntos o no se crea nada (transacción).
        with transaction.atomic():
            if es_control:
                # Paciente ya registrado: actualizamos los datos que la
                # recepcionista haya editado (todos son editables en el modal).
                paciente = paciente_existente
                if nombre: paciente.nombres = nombre
                if email: paciente.email = email
                if tipo_sangre: paciente.tipo_sangre = tipo_sangre
                if telefono: paciente.telefono = telefono
                if fecha_nac: paciente.fecha_nacimiento = fecha_nac
                paciente.tiene_seguro = tiene_seguro
                paciente.nombre_seguro = nombre_seguro if tiene_seguro else ''
                paciente.save()
            else:
                # Cita nueva: paciente nuevo garantizado (ya bloqueamos duplicados).
                paciente = Paciente.objects.create(
                    cedula=cedula,
                    nombres=nombre,
                    email=email,
                    tipo_sangre=tipo_sangre,
                    telefono=telefono,
                    fecha_nacimiento=fecha_nac,
                    tiene_seguro=tiene_seguro,
                    nombre_seguro=nombre_seguro if tiene_seguro else '',
                    nacionalidad='V'
                )

            # 4. Crear la Cita
            cita = Cita.objects.create(
                paciente=paciente,
                medico_id=medico_id,
                fecha=fecha,
                hora=hora,
                motivo=request.POST.get('motivo'),
                estado='Pendiente',
                es_control=es_control
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

        # 5-bis. CUENTA ABIERTA: se genera la deuda AHORA (factura pendiente),
        # igual que hace farmacia al despachar a crédito. Sin esto, los
        # servicios seleccionados se perdían y la Caja Central no mostraba nada
        # al buscar al paciente. La factura se enlaza a la cita y sus precios
        # se toman del catálogo (no del navegador). Al buscar la cédula en
        # caja, esta deuda aparece junto a cualquier otra pendiente del px
        # (p. ej. una compra de farmacia), y todas se cobran/cierran juntas.
        if servicios_ids:
            servicios_cobrables = CatalogoServicio.objects.filter(id__in=servicios_ids, activo=True)
            if servicios_cobrables.exists():
                with transaction.atomic():
                    factura = Factura.objects.create(
                        paciente=paciente,
                        nombre_cliente=paciente.nombres,
                        cedula_cliente=paciente.cedula,
                        cita=cita,
                        total=Decimal('0.00'),
                        estado='Pendiente'
                    )
                    total_cuenta = Decimal('0.00')
                    for servicio in servicios_cobrables:
                        total_cuenta += servicio.precio_usd
                        DetalleFactura.objects.create(
                            factura=factura,
                            departamento=servicio.categoria,
                            descripcion=servicio.nombre,
                            cantidad=1,
                            precio_unitario=servicio.precio_usd,
                            subtotal=servicio.precio_usd
                        )
                    factura.total = total_cuenta
                    factura.save(update_fields=['total'])

        messages.success(request, "Cita agendada exitosamente.")
        return redirect('dashboard_admin')

    # GET: Cargar datos para el frontend (Añadimos telefono y fecha_nacimiento a la consulta)
    catalogo = list(CatalogoServicio.objects.filter(activo=True).values('id', 'nombre', 'precio_usd', 'categoria'))
    # Los pacientes ya NO se vuelcan completos al template: el buscador
    # consulta la API api_buscar_pacientes a medida que se escribe.

    context = {
        'medicos': Medico.objects.all(),
        'fecha_actual': timezone.now().date().strftime('%Y-%m-%d'),
        'catalogo_json': json.dumps(catalogo, default=str)
    }
    return render(request, 'administracion/agendar_cita.html', context)

@login_required
@rol_requerido(['admin'])
def registrar_orden_externa(request):
    examenes_lab = [
        'Hematología', 'Glicemia', 'Urea', 'Creatinina', 'Ácido Úrico',
        'Colesterol', 'Triglicéridos', 'Perfil Lipídico', 'PT', 'PTT',
        'Fibrinógeno', 'HIV', 'VDRL', 'VSG', 'HCG cualitativa', 'PCR',
        'Proteína T y F', 'Calcio', 'Fósforo', 'Mágnesio', 'TGO - TGP',
        'Bilirrubina', 'Fosfatasa alcalina', 'Drogas de abuso', 'Heces', 'Orina'
    ]
    examenes_img = ['Rayos X', 'Ecosonograma']

    if request.method == 'POST':
        nombre = normalizar_nombre(request.POST.get('nombre'))
        nacionalidad = (request.POST.get('nacionalidad') or 'V').strip()
        cedula = normalizar_cedula(request.POST.get('cedula'))

        seleccionados = request.POST.getlist('examenes')
        otros = (request.POST.get('otros_detalle') or '').strip()
        correo_paciente = (request.POST.get('correo_paciente') or '').strip()  # <-- CORREO

        if not nombre or not cedula:
            messages.error(request, "Debe indicar el nombre y la cédula del paciente.")
            return redirect('registrar_orden_externa')
        if not cedula_es_valida(cedula):
            messages.error(request, "La cédula no es válida: debe ser numérica y no superar los 40.000.000.")
            return redirect('registrar_orden_externa')
        if not nombre_es_valido(nombre):
            messages.error(request, "El nombre no es válido: 2 a 60 caracteres, solo letras y espacios.")
            return redirect('registrar_orden_externa')
        if not seleccionados:
            messages.error(request, "Debe seleccionar al menos un examen o estudio de imagenología.")
            return redirect('registrar_orden_externa')

        paciente, _ = Paciente.objects.get_or_create(
            cedula=cedula,
            defaults={'nombres': nombre, 'nacionalidad': nacionalidad, 'email': correo_paciente}  # <-- CORREO
        )
        if correo_paciente and paciente.email != correo_paciente:  # <-- CORREO (bloque)
            paciente.email = correo_paciente
            paciente.save()

        examenes_texto = ", ".join([e for e in seleccionados if e != 'Otros'])
        SolicitudExamen.objects.create(
            paciente=paciente,
            nombre_paciente=nombre,
            cedula_paciente=f"{nacionalidad}-{cedula}",
            medico=None,
            examenes_solicitados=examenes_texto,
            otros=otros if 'Otros' in seleccionados else '',
            correo_paciente=correo_paciente or None,  # <-- CORREO
            procesar_en_lab=True,
            estado='Pendiente',
        )

        servicios_ids = list(
            CatalogoServicio.objects.filter(
                activo=True,
                categoria__in=['Laboratorio', 'Imagenologia'],
                nombre__in=seleccionados,
            ).values_list('id', flat=True)
        )

        request.session['carrito_express'] = {
            'paciente_cedula': cedula,
            'servicios_ids': servicios_ids,
        }

        messages.success(request, f"Orden enviada al laboratorio. Procese el cobro de {nombre} en caja.")
        return redirect('caja_central')

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
        cedula = normalizar_cedula(request.POST.get('cedula'))
        if not cedula_es_valida(cedula):
            messages.error(request, "La cédula no es válida: debe ser numérica y no superar los 40.000.000.")
            return redirect('editar_cita', id_cita=id_cita)
        paciente.cedula = cedula
        nombre_editado = normalizar_nombre(request.POST.get('nombre_nuevo')) or paciente.nombres
        if not nombre_es_valido(nombre_editado):
            messages.error(request, "El nombre no es válido: 2 a 60 caracteres, solo letras y espacios.")
            return redirect('editar_cita', id_cita=id_cita)
        paciente.nombres = nombre_editado
        paciente.email = (request.POST.get('email') or '').strip()
        paciente.telefono = (request.POST.get('telefono') or '').strip()
        paciente.tipo_sangre = (request.POST.get('tipo_sangre') or '').strip()

        f_nac = (request.POST.get('fecha_nacimiento') or '').strip()
        paciente.fecha_nacimiento = f_nac if f_nac else None

        tiene_seguro = request.POST.get('tiene_seguro') == 'on'
        paciente.tiene_seguro = tiene_seguro
        paciente.nombre_seguro = (request.POST.get('nombre_seguro') or '').strip() if tiene_seguro else ''

        try:
            paciente.save()
        except Exception as e:
            messages.error(request, "Error al actualizar los datos del paciente: La cédula podría ya estar registrada.")
            return redirect('editar_cita', id_cita=id_cita)

        # Actualizar Datos de la Cita
        # NOTA: el estado NO se toca aquí. El selector manual se eliminó del
        # template y el estado es automático (Pendiente -> Atendido al atender).
        # Leerlo del POST asignaba None y borraba el estado de la cita.
        medico_id = request.POST.get('medico_id')
        if medico_id:
            cita.medico_id = medico_id

        fecha_post = request.POST.get('fecha')
        if fecha_post:
            cita.fecha = fecha_post
        hora_post = request.POST.get('hora')
        if hora_post:
            cita.hora = hora_post
        cita.motivo = request.POST.get('motivo') or cita.motivo

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
            # Respaldo inteligente: la última tasa con la que se trabajó es
            # muchísimo más cercana a la realidad que un número fijo.
            ultima_sesion = SesionCaja.objects.order_by('-fecha_apertura').first()
            if ultima_sesion:
                tasa_actual = ultima_sesion.tasa_bcv_dia
                messages.warning(request, f"No se pudo consultar la tasa del BCV. Se aplicó la ÚLTIMA TASA REGISTRADA ({tasa_actual} Bs/$). Verifíquela contra el BCV antes de cobrar en bolívares.")
            else:
                tasa_actual = Decimal('500.00')
                messages.warning(request, "No se pudo conectar al BCV y no hay tasas previas registradas. Se aplicó una tasa de respaldo de 500 Bs/$: VERIFÍQUELA antes de cobrar.")

        sesion = SesionCaja.objects.create(
            cajero=request.user,
            tasa_bcv_dia=tasa_actual,
            estado='Abierta'
        )

    # 2. PROCESAMIENTO DEL PAGO (Vía AJAX)
    if request.method == 'POST' and request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        try:
            data = json.loads(request.body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return JsonResponse({'status': 'error', 'message': 'La solicitud llegó dañada. Recargue la página e intente de nuevo.'}, status=400)

        try:
            cedula = normalizar_cedula(data.get('cedula'))
            nombre = normalizar_nombre(data.get('nombre'))
            correo = (data.get('correo') or '').strip()
            carrito = data.get('carrito') or []
            pagos = data.get('pagos') or []
            facturas_pendientes_ids = data.get('facturas_pendientes') or []
            cita_id = data.get('cita_id')

            # --- VALIDACIONES PREVIAS (nada toca la BD todavía) ---
            if not nombre or not cedula_es_valida(cedula):
                return JsonResponse({'status': 'error', 'message': 'Debe indicar un nombre y una cédula válida (numérica, máx. 40.000.000).'}, status=400)
            if not nombre_es_valido(nombre):
                return JsonResponse({'status': 'error', 'message': 'El nombre no es válido: 2 a 60 caracteres, solo letras y espacios.'}, status=400)

            # Separar servicios nuevos (del catálogo) de los ítems "pendiente_"
            # que solo representan deudas viejas ya facturadas.
            items_nuevos = [item for item in carrito if not str(item.get('id', '')).startswith('pendiente_')]

            if not items_nuevos and not facturas_pendientes_ids:
                return JsonResponse({'status': 'error', 'message': 'El carrito está vacío: no hay nada que cobrar.'}, status=400)
            if not pagos:
                return JsonResponse({'status': 'error', 'message': 'Debe registrar al menos un método de pago.'}, status=400)

            metodos_validos = dict(PagoFactura.METODOS)

            # --- TODO EL COBRO ES ATÓMICO: factura + detalles + pagos se
            #     registran completos o no se registra nada. ---
            with transaction.atomic():
                cita_obj = Cita.objects.filter(id=cita_id).first() if cita_id else None

                # 1. Crear o recuperar paciente de emergencia
                paciente, created = Paciente.objects.get_or_create(
                    cedula=cedula,
                    defaults={'nombres': nombre, 'nacionalidad': 'V'}
                )

                # 2. Marcar las facturas viejas (Farmacia/Médico) como "Pagadas".
                #    SOLO se aceptan facturas que sigan Pendientes: así un doble
                #    clic o un id manipulado no puede "re-pagar" ni tocar
                #    facturas de otro estado. select_for_update evita que dos
                #    cajeros cobren la misma deuda a la vez.
                facturas_viejas = []
                if facturas_pendientes_ids:
                    facturas_viejas = list(
                        Factura.objects.select_for_update()
                        .filter(id__in=facturas_pendientes_ids, estado='Pendiente')
                    )
                    if len(facturas_viejas) != len(set(facturas_pendientes_ids)):
                        raise ValueError('Alguna de las facturas pendientes ya fue cobrada o no existe. Recargue la página para actualizar las deudas.')

                    campos_update = {
                        'estado': 'Pagada',
                        'fecha_pago': timezone.now(),
                        'paciente': paciente,
                        'nombre_cliente': nombre,
                        'cedula_cliente': cedula,
                    }
                    if cita_obj:
                        campos_update['cita'] = cita_obj
                    Factura.objects.filter(id__in=[f.id for f in facturas_viejas]).update(**campos_update)

                # 3. Servicios NUEVOS desde el catálogo de la Caja.
                #    El precio se toma SIEMPRE de la base de datos (CatalogoServicio),
                #    nunca del JSON del navegador: un catálogo desactualizado en el
                #    frontend o una petición manipulada no pueden alterar el cobro.
                factura_maestra = None
                if items_nuevos:
                    ids_solicitados = []
                    for item in items_nuevos:
                        cantidad = int(item.get('cantidad', 1))
                        if cantidad <= 0:
                            raise ValueError('Hay una cantidad inválida en el carrito.')
                        ids_solicitados.append(str(item.get('id')))

                    servicios_db = {
                        str(s.id): s
                        for s in CatalogoServicio.objects.filter(id__in=ids_solicitados, activo=True)
                    }
                    faltantes = set(ids_solicitados) - set(servicios_db.keys())
                    if faltantes:
                        raise ValueError('Uno de los servicios del carrito ya no está disponible en el catálogo. Recargue la página.')

                    factura_maestra = Factura.objects.create(
                        paciente=paciente,
                        nombre_cliente=nombre,
                        cedula_cliente=cedula,
                        total=Decimal('0.00'),
                        estado='Pagada',
                        fecha_pago=timezone.now(),
                        cita=cita_obj
                    )

                    total_nuevos = Decimal('0.00')
                    for item in items_nuevos:
                        servicio = servicios_db[str(item.get('id'))]
                        cantidad = int(item.get('cantidad', 1))
                        precio_u = servicio.precio_usd
                        subtotal = precio_u * cantidad
                        total_nuevos += subtotal

                        DetalleFactura.objects.create(
                            factura=factura_maestra,
                            departamento=servicio.categoria,
                            descripcion=servicio.nombre,
                            cantidad=cantidad,
                            precio_unitario=precio_u,
                            subtotal=subtotal
                        )

                    factura_maestra.total = total_nuevos
                    factura_maestra.save(update_fields=['total'])

                # 4. Registrar los pagos en la factura generada (o en la primera deuda)
                factura_destino = factura_maestra if factura_maestra else facturas_viejas[0]

                metodos_usados = []
                for p in pagos:
                    metodo = p.get('metodo')
                    if metodo not in metodos_validos:
                        raise ValueError('Se recibió un método de pago no reconocido.')
                    monto_original = Decimal(str(p.get('monto_ingresado')))
                    equivalente = Decimal(str(p.get('equivalente_usd')))
                    if monto_original <= 0 or equivalente < 0:
                        raise ValueError('Los montos de pago deben ser mayores a cero.')

                    PagoFactura.objects.create(
                        factura=factura_destino,
                        sesion=sesion,
                        metodo=metodo,
                        monto_moneda_original=monto_original,
                        monto_equivalente_usd=equivalente,
                        referencia=p.get('referencia', '')
                    )
                    metodos_usados.append(metodo)

                if metodos_usados:
                    factura_destino.metodo_pago = ', '.join(dict.fromkeys(metodos_usados))[:50]
                    factura_destino.save(update_fields=['metodo_pago'])

            # --- COMPROBANTE POR CORREO (fuera de la transacción: el pago ya
            #     quedó firme; si el correo falla, NO se revierte nada) ---
            correo_aviso = None
            correo_enviado = None
            if correo:
                if correo_es_valido(correo):
                    try:
                        # Resumen de TODO lo pagado en esta transacción: deudas
                        # viejas saldadas + servicios nuevos del catálogo.
                        todas_facturas = list(facturas_viejas)
                        if factura_maestra:
                            todas_facturas.append(factura_maestra)

                        lineas_comp = []
                        total_comp = Decimal('0.00')
                        for f in todas_facturas:
                            total_comp += f.total
                            for d in f.detalles.all():
                                lineas_comp.append({
                                    'descripcion': d.descripcion,
                                    'cantidad': d.cantidad,
                                    'precio_unitario': d.precio_unitario,
                                    'subtotal': d.subtotal,
                                })

                        pagos_comp = []
                        for p in pagos:
                            metodo = p.get('metodo')
                            pagos_comp.append({
                                'metodo': metodo,
                                'moneda': 'USD' if metodo in _METODOS_USD else 'Bs',
                                'monto_original': Decimal(str(p.get('monto_ingresado'))),
                                'monto_usd': Decimal(str(p.get('equivalente_usd'))),
                            })

                        enviar_comprobante_pago(
                            destinatario=correo,
                            origen='Caja Central',
                            cliente_nombre=nombre,
                            cliente_cedula=cedula,
                            numero=factura_destino.numero_factura or f"FAC-{factura_destino.id}",
                            fecha=factura_destino.fecha_pago or timezone.now(),
                            lineas=lineas_comp,
                            total_usd=total_comp,
                            pagos=pagos_comp,
                        )
                        correo_enviado = correo
                    except Exception:
                        # Nunca tumbar la respuesta del pago por un fallo del correo.
                        logger.exception("Pago OK pero falló el armado/envío del comprobante (caja central)")
                        correo_aviso = "El pago se registró, pero no se pudo enviar el comprobante por correo."
                else:
                    correo_aviso = "No se envió el comprobante: el correo ingresado no es válido."

            return JsonResponse({
                'status': 'success',
                'factura_id': factura_destino.id,
                'correo_aviso': correo_aviso,
                'correo_enviado': correo_enviado,
            })

        except ValueError as e:
            # Errores de negocio esperables: el mensaje es seguro de mostrar.
            return JsonResponse({'status': 'error', 'message': str(e)}, status=400)
        except (InvalidOperation, TypeError, KeyError):
            return JsonResponse({'status': 'error', 'message': 'Los datos del pago llegaron incompletos o con formato inválido.'}, status=400)
        except Exception:
            # Cualquier otra cosa: queda en logs/errores.log, al cajero solo
            # le llega un mensaje genérico (sin trazas internas del sistema).
            logger.exception("Error inesperado procesando un pago en caja_central")
            return JsonResponse({'status': 'error', 'message': 'Ocurrió un error interno al procesar el pago. El detalle quedó registrado en el sistema.'}, status=500)

    # Sacamos los datos de la sesión (usamos pop para que se limpie tras el primer uso)
    carrito_express = request.session.pop('carrito_express', None)
    paciente_express = None
    servicios_express = []
    cita_express_id = None

    if carrito_express:
        cedula_ex = carrito_express.get('paciente_cedula')
        paciente_express = Paciente.objects.filter(cedula=cedula_ex).first()
        servicios_ids = carrito_express.get('servicios_ids', [])
        cita_express_id = carrito_express.get('cita_id')
        
        # Obtenemos los datos mínimos necesarios de los servicios para el JS
        servicios_express = list(CatalogoServicio.objects.filter(id__in=servicios_ids).values('id', 'nombre', 'precio_usd'))

    # 3. GET: ENVIAR DATOS AL FRONTEND
    catalogo = list(CatalogoServicio.objects.filter(activo=True).values('id', 'nombre', 'categoria', 'precio_usd'))
    # Pacientes vía api_buscar_pacientes (búsqueda incremental), no por volcado.

    context = {
        'sesion': sesion,
        'tasa_bcv': float(sesion.tasa_bcv_dia),
        'catalogo_json': json.dumps(catalogo, default=str),
        'paciente_express': paciente_express,
        'servicios_express': json.dumps(servicios_express, default=str),
        'cita_express_id': cita_express_id if cita_express_id else 'null'
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
@require_POST
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
@require_POST
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
    paginator = Paginator(citas_query, 5)
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
def api_buscar_pacientes(request):
    """
    Búsqueda incremental de pacientes para los buscadores de Agendar Cita y
    Caja Central. Sustituye el volcado de TODOS los pacientes a JSON en cada
    carga de página (que crecía con la base de datos y exponía el padrón
    completo de pacientes en el código fuente de la página).
    Devuelve como máximo 8 coincidencias por nombre o cédula.
    """
    q = (request.GET.get('q') or '').strip()
    if len(q) < 2:
        return JsonResponse({'resultados': []})

    filtro = Q(nombres__icontains=q)
    q_digitos = normalizar_cedula(q)
    if q_digitos:
        filtro |= Q(cedula__startswith=q_digitos)

    pacientes = list(
        Paciente.objects.filter(filtro)
        .order_by('nombres')
        .values('id', 'cedula', 'nombres', 'email', 'tipo_sangre', 'telefono', 'fecha_nacimiento')[:8]
    )
    for p in pacientes:
        if p['fecha_nacimiento']:
            p['fecha_nacimiento'] = p['fecha_nacimiento'].strftime('%Y-%m-%d')

    return JsonResponse({'resultados': pacientes})

@login_required
@rol_requerido(['admin'])
def obtener_deudas_paciente(request, cedula):
    """ Busca si el paciente tiene facturas pendientes en Farmacia, Lab o Médico """
    # La cédula puede llegar como 'V-12.345.678' desde el buscador: se lleva
    # al formato canónico (solo dígitos) para que el cruce con
    # Factura.cedula_cliente nunca falle por formato.
    cedula = normalizar_cedula(cedula)
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
def cuentas_abiertas(request):
    """ Lista de pacientes con facturas pendientes (cuentas por cobrar) para la caja.
        Se agrupa por cedula_cliente para ser consistente con obtener_deudas_paciente. """
    pendientes = (Factura.objects
                  .filter(estado='Pendiente')
                  .exclude(cedula_cliente__isnull=True)
                  .exclude(cedula_cliente='')
                  .select_related('paciente'))

    cuentas = {}
    for fac in pendientes:
        cedula = fac.cedula_cliente
        if cedula not in cuentas:
            cuentas[cedula] = {
                'cedula': cedula,
                'nombre': fac.nombre_cliente or (fac.paciente.nombres if fac.paciente else 'Sin nombre'),
                'total': Decimal('0.00'),
                'num_facturas': 0,
            }
        cuentas[cedula]['total'] += (fac.total or Decimal('0.00'))
        cuentas[cedula]['num_facturas'] += 1

    # Mayor deuda primero; Decimal -> float para serializar a JSON
    lista = sorted(cuentas.values(), key=lambda c: c['total'], reverse=True)
    for c in lista:
        c['total'] = float(c['total'])

    return JsonResponse({'status': 'success', 'cuentas': lista})

@login_required
@rol_requerido(['admin'])
@require_POST
def cerrar_caja(request):
    """ Calcula los totales de la sesión actual, la cierra y redirige al reporte """
    sesion = SesionCaja.objects.filter(cajero=request.user, estado='Abierta').first()
    
    if not sesion:
        messages.error(request, "No tiene una caja abierta actualmente.")
        return redirect('dashboard_admin')
        
    # Pagos de ESTA sesión únicamente. El filtro anterior
    # (fecha_pago__gte=apertura) sumaba los pagos de TODOS los cajeros del
    # período: si dos cajas estaban abiertas a la vez, ambos arqueos
    # reportaban el dinero del otro.
    pagos = PagoFactura.objects.filter(sesion=sesion)
    
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
    
    # Convertimos los bolívares a dólares usando la tasa del día en que se abrió la caja.
    # Todo en Decimal (no float) para que el arqueo no acumule errores de redondeo.
    if sesion.tasa_bcv_dia:
        gran_total_usd = total_usd_puro + (total_bs / sesion.tasa_bcv_dia)
    else:
        gran_total_usd = total_usd_puro
    gran_total_usd = gran_total_usd.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    
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
    paginator = Paginator(facturas, 7)
    page_number = request.GET.get('page')
    facturas_paginadas = paginator.get_page(page_number)

    context = {
        'facturas': facturas_paginadas,
        'query': query,
        'fecha_filtro': fecha_filtro
    }
    return render(request, 'administracion/historico_caja.html', context)

def _calcular_estadisticas(periodo='mes'):
    """
    Cálculo puro de las 4 estadísticas del dashboard. Lo comparten la API JSON,
    el export a Excel y el PDF, sin pasar por HTTP ni re-parsear JSON.
    """
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
    # Excluimos las externas para que la estadística refleje solo la carga interna del ambulatorio.
    ordenes = SolicitudExamen.objects.filter(fecha_solicitud__gte=inicio, procesar_en_lab=True)
    todos_examenes_lista = []
    for orden in ordenes:
        if orden.examenes_solicitados:
            lista_temp = [e.strip() for e in orden.examenes_solicitados.split(',') if e.strip()]
            todos_examenes_lista.extend(lista_temp)

    contador = Counter(todos_examenes_lista)
    top_diez = contador.most_common(10)

    examenes_final = []
    for item in top_diez:
        examenes_final.append({
            'descripcion': item[0],
            'total': item[1]
        })

    return {
        'morbilidad': morbilidad,
        'flujo': flujo,
        'medicamentos': medicamentos,
        'examenes': examenes_final
    }

@login_required
@rol_requerido(['admin'])
def datos_estadisticas(request):
    try:
        periodo = request.GET.get('periodo', 'mes')
        return JsonResponse(_calcular_estadisticas(periodo))
    except Exception:
        # El detalle completo (con traza) va al log rotativo, no al print ni al cliente.
        logger.exception("Error calculando estadísticas de administración")
        return JsonResponse({'error': 'No se pudieron calcular las estadísticas.'}, status=500)

@login_required
@rol_requerido(['admin'])
def exportar_excel_estadisticas(request, tipo):
    """ Genera un Excel con los datos y DIBUJA un gráfico nativo dentro de Excel """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = f"Reporte {tipo.capitalize()}"

    # Preparamos las columnas
    ws.append(["Categoría", "Cantidad"])

    # Whitelist: un tipo inventado en la URL daría KeyError (500). Con esto,
    # devuelve un 404 limpio.
    TIPOS_VALIDOS = ('morbilidad', 'flujo', 'medicamentos', 'examenes')
    if tipo not in TIPOS_VALIDOS:
        raise Http404("Tipo de reporte no válido.")

    # Reutilizamos el cálculo directamente (sin simular una petición HTTP interna)
    periodo = request.GET.get('periodo', 'mes')
    datos = _calcular_estadisticas(periodo)[tipo]

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
    # Reutilizamos el cálculo directamente para que Jinja pinte los datos en el HTML
    context = _calcular_estadisticas(request.GET.get('periodo', 'mes'))
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
            # Validación explícita: al asignar directo al campo y llamar a save(),
            # Django NO ejecuta los validators del modelo. Sin esto se podría
            # guardar cualquier archivo disfrazado de imagen en /media/.
            if 'foto_perfil' in request.FILES:
                try:
                    validar_imagen(request.FILES['foto_perfil'])
                except ValidationError as err:
                    messages.error(request, f"Foto rechazada: {' '.join(err.messages)}")
                    return redirect('editar_perfil_admin')
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

    # Sin médico o sin fecha no hay nada que verificar (evita un 500 por
    # parámetros vacíos en la URL).
    if not medico_id or not fecha:
        return JsonResponse({'ocupadas': []})

    # Buscamos las horas ya ocupadas para ese médico en ese día.
    # ('En Sala' se eliminó de Cita.ESTADOS: los estados vigentes son
    #  Pendiente y Atendido.)
    horas_ocupadas = list(Cita.objects.filter(
        medico_id=medico_id,
        fecha=fecha,
        estado__in=['Pendiente', 'Atendido']
    ).values_list('hora', flat=True))
    
    # Formateamos las horas (HH:MM) para que JS las reconozca
    horas_formateadas = [h.strftime('%H:%M') for h in horas_ocupadas]
    
    return JsonResponse({'ocupadas': horas_formateadas})
