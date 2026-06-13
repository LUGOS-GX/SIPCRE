from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.http import JsonResponse, HttpResponse
from django.contrib import messages
from django.db.models import F, Q, Sum, Count
from django.db.models.functions import TruncDate
from django.db import transaction
from django.core.paginator import Paginator
from django.utils import timezone
from datetime import timedelta, date, datetime
from decimal import Decimal
from .models import OrdenFarmacia, Medicamento, DetalleDespacho, LoteMedicamento, MovimientoInventario, AuditoriaControlado
from administracion.models import Factura, DetalleFactura, SesionCaja
from administracion.utils import obtener_tasa_bcv
from .forms import MedicamentoForm, LoteMedicamentoForm
from usuarios.decorators import rol_requerido
from core.validators import normalizar_cedula, cedula_es_valida
from .services import descontar_lotes_fefo, reintegrar_lotes
import json
import io
import os
import base64
import xlsxwriter
import logging
logger = logging.getLogger('sipcre')


class ReglaNegocioError(Exception):
    """
    Error de regla de negocio cuyo mensaje SÍ es seguro mostrar al usuario
    (stock insuficiente, falta de validación de un controlado, etc.).
    Se diferencia de un error inesperado (bug, fallo de BD) para no filtrar
    detalles internos del sistema al cliente.
    """
    pass


@login_required
@rol_requerido(['farmacia'])
def dashboard_farmacia(request):
    # --- 1. CAPTURAR LA BÚSQUEDA ---
    query = request.GET.get('q', '')
    
    # 2. Base de datos: Órdenes pendientes
    ordenes_pendientes_list = OrdenFarmacia.objects.filter(estado='Pendiente').select_related('paciente', 'medico').order_by('-fecha_solicitud')
    
    # --- 3. APLICAR BÚSQUEDA INTELIGENTE EN BACKEND ---
    if query:
        ordenes_pendientes_list = ordenes_pendientes_list.filter(
            Q(paciente__nombres__icontains=query) |
            Q(paciente__cedula__icontains=query) |
            Q(nombre_paciente__icontains=query) |
            Q(cedula_paciente__icontains=query) |
            Q(id__icontains=query)
        )

    # --- 4. PAGINACIÓN ---
    paginator = Paginator(ordenes_pendientes_list, 5) 
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    # 5. Historial despachado (Limitamos a los últimos 15 para no saturar)
    ordenes_despachadas = OrdenFarmacia.objects.filter(estado='Despachado').select_related('paciente', 'medico').order_by('-fecha_despacho')[:15]
    
    # 6. Alertas automáticas de inventario
    medicamentos_alerta = Medicamento.objects.filter(stock_actual__lte=F('stock_minimo')).order_by('stock_actual')

    context = {
        'page_obj': page_obj, 
        'ordenes_despachadas': ordenes_despachadas,
        'medicamentos_alerta': medicamentos_alerta,
        'query': query, # Devolvemos la palabra buscada para que no se borre de la barra
    }
    return render(request, 'farmacia/dashboard.html', context)

@login_required
@rol_requerido(['farmacia'])
def editar_perfil_farmacia(request):
    usuario = request.user 

    if request.method == 'POST':
        try:
            # Procesar el Nombre
            nombre_completo = request.POST.get('nombre_completo', '').strip()
            if nombre_completo:
                partes = nombre_completo.split(' ', 1)
                usuario.first_name = partes[0]
                usuario.last_name = partes[1] if len(partes) > 1 else ''

            # Procesar Cédula y Teléfono
            usuario.cedula = request.POST.get('cedula', usuario.cedula)
            usuario.telefono = request.POST.get('telefono', usuario.telefono)
            
            usuario.save()

            messages.success(request, "Perfil actualizado correctamente.")
            return redirect('editar_perfil_farmacia')

        except Exception as e:
            messages.error(request, f"Error al guardar los datos: {str(e)}")

    context = {
        'usuario': usuario,
    }
    return render(request, 'farmacia/editar_perfil.html', context)

@login_required
@rol_requerido(['farmacia'])
def despachar_orden(request, orden_id):
    orden = get_object_or_404(OrdenFarmacia, id=orden_id)
    
    if orden.estado != 'Pendiente':
        messages.warning(request, "Esta orden ya fue despachada.")
        return redirect('dashboard_farmacia')

    medicamentos_db = Medicamento.objects.filter(stock_actual__gt=0).order_by('nombre')
    catalogo = [
        {
            'id': m.id,
            'nombre': m.nombre,
            'presentacion': m.presentacion if m.presentacion else '',
            'concentracion': m.concentracion if m.concentracion else '',
            'stock': m.stock_actual,
            'precio': float(m.precio) if m.precio else 0.0,
            'es_controlado': m.es_controlado
        } for m in medicamentos_db
    ]

    if request.method == 'POST':
        accion = request.POST.get('accion')
        meds_ids = request.POST.getlist('medicamento_id[]')
        cantidades = request.POST.getlist('cantidad[]')

        if not meds_ids:
            messages.error(request, "Debe agregar medicamentos al carrito.")
            return redirect('despachar_orden', orden_id=orden.id)

        nombre_cli = orden.nombre_paciente if orden.nombre_paciente else (orden.paciente.nombres if orden.paciente else 'Paciente Desconocido')
        # Formato canónico (solo dígitos): si la factura queda 'Pendiente',
        # la Caja Central la busca por esta cédula y el formato DEBE coincidir.
        cedula_cli = normalizar_cedula(
            orden.cedula_paciente if orden.cedula_paciente else (orden.paciente.cedula if orden.paciente else '')
        ) or '0000000'

        estado_factura = 'Pagada' if accion == 'pagar_farmacia' else 'Pendiente'

        # Método de cobro simulado (solo dual $/Bs). Solo aplica cuando el pago
        # es inmediato; en cuenta abierta el cobro se hará luego en Caja Central.
        metodo_pago = None
        if estado_factura == 'Pagada':
            metodo_raw = (request.POST.get('metodo_pago') or 'USD').upper()
            metodo_pago = 'Bs' if metodo_raw in ('BS', 'BOLIVARES', 'BOLÍVARES') else '$'

        # Todo el despacho va dentro de una transacción: o se registra completo
        # (factura + stock + kardex + auditoría) o no se registra nada.
        with transaction.atomic():
            factura = Factura.objects.create(
                nombre_cliente=nombre_cli,
                cedula_cliente=cedula_cli,
                total=Decimal('0.00'),
                estado=estado_factura
            )

            total_acumulado = Decimal('0.00')
            items_despachados = 0
            omitidos = []

            for med_id, cant_raw in zip(meds_ids, cantidades):
                # Validar la cantidad recibida (el POST puede venir manipulado)
                try:
                    cant = int(cant_raw)
                except (TypeError, ValueError):
                    continue
                if cant <= 0 or not str(med_id).isdigit():
                    continue

                # select_for_update bloquea la fila del medicamento hasta cerrar
                # la transacción, evitando que dos despachos simultáneos vendan
                # el mismo stock (condición de carrera / sobreventa).
                med = Medicamento.objects.select_for_update().filter(id=med_id).first()
                if med is None:
                    continue

                if cant > med.stock_actual:
                    omitidos.append(f"{med.nombre} (stock insuficiente)")
                    continue

                stock_antes = med.stock_actual
                med.stock_actual -= cant
                med.save(update_fields=['stock_actual'])
                # Consumo de lotes por vencimiento más próximo (FEFO)
                descontar_lotes_fefo(med, cant)

                # Auditoría para medicamentos controlados
                if med.es_controlado:
                    AuditoriaControlado.objects.create(
                        medicamento=med,
                        usuario_despacho=request.user,
                        orden=orden,
                        nombre_paciente=nombre_cli,
                        cedula_paciente=cedula_cli,
                        cantidad_despachada=cant,
                        stock_antes=stock_antes,
                        stock_despues=med.stock_actual,
                        ip_origen=request.META.get('REMOTE_ADDR'),
                    )
                    logger.warning(f"CONTROLADO DESPACHADO | medicamento={med.nombre} | cantidad={cant} | paciente={cedula_cli} | usuario={request.user.email} | orden={orden.id}")

                # Aritmética de dinero en Decimal (no float) para no acumular
                # errores de redondeo en la facturación.
                precio_unit = med.precio if med.precio else Decimal('0.00')
                subtotal = precio_unit * cant
                total_acumulado += subtotal

                # Referencia del kardex: incluye monto, y el método de pago solo
                # si el despacho se cobró de una vez (en cuenta abierta el pago
                # ocurre después en Caja Central).
                if metodo_pago:
                    referencia_kardex = f"Despacho c/pago | ${subtotal:.2f} | Pago: {metodo_pago} | Orden #{orden.id}"
                else:
                    referencia_kardex = f"Despacho (cuenta abierta) | ${subtotal:.2f} | Orden #{orden.id}"

                MovimientoInventario.objects.create(
                    medicamento=med,
                    tipo_movimiento='SALIDA',
                    cantidad=-cant,
                    stock_resultante=med.stock_actual,
                    referencia=referencia_kardex,
                    orden_relacionada=orden,
                    usuario=request.user
                )

                DetalleFactura.objects.create(
                    factura=factura,
                    descripcion=f"{med.nombre} {med.concentracion}",
                    cantidad=cant,
                    precio_unitario=precio_unit,
                    subtotal=subtotal
                )
                items_despachados += 1

            # Si no se pudo despachar nada, revertimos todo (sin factura vacía
            # ni la orden marcada como despachada).
            if items_despachados == 0:
                transaction.set_rollback(True)
                messages.error(request, "No se despachó ningún medicamento. Verifica el stock y las cantidades del carrito.")
                return redirect('despachar_orden', orden_id=orden.id)

            factura.total = total_acumulado
            factura.save(update_fields=['total'])

            orden.estado = 'Despachado'
            orden.save(update_fields=['estado'])

        msg = f"Orden #{orden.id} finalizada. Factura #{factura.id} generada."
        if omitidos:
            msg += " No se incluyeron (stock insuficiente): " + ", ".join(omitidos) + "."
        messages.success(request, msg)
        return redirect('dashboard_farmacia')

    # Tasa BCV para el cobro dual ($/Bs) simulado, igual que en la Caja de
    # Farmacia. Respaldo a la última sesión de caja si la API no responde.
    tasa_bcv = obtener_tasa_bcv()
    if not tasa_bcv:
        ultima_sesion = SesionCaja.objects.order_by('-fecha_apertura').first()
        tasa_bcv = ultima_sesion.tasa_bcv_dia if ultima_sesion else Decimal('0.00')

    context = {
        'orden': orden,
        'catalogo_json': json.dumps(catalogo),
        'tasa_bcv': float(tasa_bcv),
    }
    return render(request, 'farmacia/despachar_orden.html', context)

@login_required
@rol_requerido(['farmacia'])
@require_POST
def cancelar_orden_farmacia(request, orden_id):
    """ Función para descartar una orden desde el dashboard de farmacia """
    orden = get_object_or_404(OrdenFarmacia, id=orden_id)
    
    # Solo permitimos cancelar si la orden aún está pendiente
    if orden.estado == 'Pendiente':
        orden.estado = 'Cancelado'
        orden.save()
        messages.warning(request, f'La Orden #{orden.id} ha sido descartada exitosamente.')
    else:
        messages.error(request, f'No se puede cancelar la Orden #{orden.id} porque ya está en estado: {orden.estado}.')
        
    return redirect('dashboard_farmacia')

@login_required
@rol_requerido(['farmacia'])
def inventario_farmacia(request):
    # 1. Capturamos la búsqueda
    query = request.GET.get('q', '')
    
    # 2. Obtenemos la lista base
    medicamentos_list = Medicamento.objects.all().order_by('nombre')
    
    # 3. Filtramos en el servidor si hay búsqueda
    if query:
        medicamentos_list = medicamentos_list.filter(
            Q(nombre__icontains=query) |
            Q(concentracion__icontains=query) |
            Q(presentacion__icontains=query)
        )
        
    # 4. Paginación: 15 medicamentos por página
    paginator = Paginator(medicamentos_list, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    return render(request, 'farmacia/inventario.html', {
        'page_obj': page_obj,
        'query': query
    })

@login_required
@rol_requerido(['farmacia'])
def agregar_medicamento(request):
    if request.method == 'POST':
        form = MedicamentoForm(request.POST, request.FILES) 
        if form.is_valid():
            with transaction.atomic():
                med = form.save()
                if med.stock_actual > 0:
                    MovimientoInventario.objects.create(
                        medicamento=med,
                        tipo_movimiento='ENTRADA',
                        cantidad=med.stock_actual,
                        stock_resultante=med.stock_actual,
                        usuario=request.user,
                        referencia="Inventario Inicial (Registro de Nuevo Medicamento)"
                    )
                    # El stock inicial ahora nace dentro de un lote real (#001), no
                    # huérfano: así FEFO puede consumirlo y la trazabilidad de
                    # vencimientos es completa desde el primer día. Si el formulario
                    # trajo fecha de vencimiento, el lote la hereda; si no, se usa
                    # una fecha placeholder lejana (visible como "sin definir") que
                    # se corrige luego en Gestión de Lotes.
                    venc = med.fecha_vencimiento or date(2099, 12, 31)
                    LoteMedicamento.objects.create(
                        medicamento=med,
                        numero_lote=LoteMedicamento.generar_numero_lote(med),
                        cantidad_ingresada=med.stock_actual,
                        cantidad_actual=med.stock_actual,
                        fecha_vencimiento=venc,
                    )
            messages.success(request, f"¡{med.nombre} agregado al inventario con éxito!")
            return redirect('inventario_farmacia')
        else:
            messages.error(request, "Hubo un error en el formulario. Por favor, revisa los datos.")
    else:
        form = MedicamentoForm()
    
    return render(request, 'farmacia/formulario_medicamento.html', {'form': form, 'titulo': 'Nuevo Medicamento'})

@login_required
@rol_requerido(['farmacia'])
def editar_medicamento(request, med_id):
    med = get_object_or_404(Medicamento, id=med_id)
    
    if request.method == 'POST':
        # instance=med le dice a Django que no cree uno nuevo, sino que actualice este
        form = MedicamentoForm(request.POST, request.FILES, instance=med)
        if form.is_valid():
            form.save()
            messages.success(request, f"¡{med.nombre} actualizado correctamente!")
            return redirect('inventario_farmacia')
        else:
            messages.error(request, "Error al actualizar. Revisa los datos.")
    else:
        form = MedicamentoForm(instance=med)
        
    return render(request, 'farmacia/formulario_medicamento.html', {'form': form, 'titulo': f'Editar {med.nombre}'})

@login_required
@rol_requerido(['farmacia'])
def eliminar_medicamento(request, med_id):
    med = get_object_or_404(Medicamento, id=med_id)
    if request.method == 'POST':
        try:
            nombre = med.nombre
            med.delete()
            messages.success(request, f"Medicamento '{nombre}' eliminado del sistema.")
        except Exception as e:
            # Si el medicamento ya se entregó en un despacho, la BD lo protege para no arruinar el historial
            messages.error(request, f"No se puede eliminar {med.nombre} porque ya está asociado a órdenes de despacho históricas.")
            
    return redirect('inventario_farmacia')

@login_required
@rol_requerido(['farmacia'])
def registrar_lote(request):
    if request.method == 'POST':
        form = LoteMedicamentoForm(request.POST)
        if form.is_valid():
            # El lote, la suma al stock y el kardex se registran juntos o no se
            # registran (transacción). El stock lo suma SOLO esta vista: el modelo
            # LoteMedicamento ya no lo hace, por eso desaparece el doble conteo.
            with transaction.atomic():
                nuevo_lote = form.save(commit=False)

                # Bloqueamos el medicamento: el número de lote es correlativo por
                # medicamento y se calcula contando los existentes, así que dos
                # registros simultáneos no deben pisarse.
                medicamento = Medicamento.objects.select_for_update().get(pk=nuevo_lote.medicamento_id)

                nuevo_lote.numero_lote = LoteMedicamento.generar_numero_lote(medicamento)
                nuevo_lote.cantidad_actual = nuevo_lote.cantidad_ingresada
                nuevo_lote.save()

                medicamento.stock_actual += nuevo_lote.cantidad_ingresada
                medicamento.save(update_fields=['stock_actual'])

                MovimientoInventario.objects.create(
                    medicamento=medicamento,
                    tipo_movimiento='ENTRADA',
                    cantidad=nuevo_lote.cantidad_ingresada,
                    stock_resultante=medicamento.stock_actual,
                    usuario=request.user,
                    referencia=f"Ingreso de Lote {nuevo_lote.numero_lote}"
                )

            messages.success(request, f"Lote {nuevo_lote.numero_lote} de {medicamento.nombre} registrado correctamente. Se sumaron {nuevo_lote.cantidad_ingresada} unidades al inventario.")
            return redirect('inventario_farmacia')
        else:
            messages.error(request, "Error al registrar el lote. Verifique los datos.")
    else:
        # Pre-seleccionamos el medicamento si viene por la URL
        med_id = request.GET.get('medicamento')
        form = LoteMedicamentoForm(initial={'medicamento': med_id}) if med_id else LoteMedicamentoForm()

    return render(request, 'farmacia/registrar_lote.html', {'form': form})

@login_required
@rol_requerido(['farmacia'])
def api_estadisticas_farmacia(request):
    periodo = request.GET.get('periodo', 'mes')
    hoy = timezone.now().date()

    if periodo == 'semana':
        fecha_inicio = hoy - timedelta(days=7)
    elif periodo == 'ano':
        fecha_inicio = hoy - timedelta(days=365)
    else: 
        fecha_inicio = hoy - timedelta(days=30)

    # El KARDEX es la fuente de la verdad absoluta. 
    # Buscamos todas las salidas ligadas a una orden (sea de paso o interna)
    salidas_kardex = MovimientoInventario.objects.filter(
        tipo_movimiento='SALIDA',
        orden_relacionada__isnull=False,
        fecha__date__gte=fecha_inicio
    )

    # 1. Medicamentos más solicitados (Top 5)
    salida_meds = salidas_kardex.values(
        nombre_med=F('medicamento__nombre'),
        concentracion=F('medicamento__concentracion')
    ).annotate(
        total_despachado=Sum('cantidad')
    ).order_by('total_despachado')[:5] 
    # Nota: Ordenamos de menor a mayor porque en el Kardex las salidas son negativas (ej. -50 es más salida que -10)

    # Mantenemos tu formato exacto: "Nombre (Concentración)"
    labels_meds = [f"{m['nombre_med']} ({m['concentracion']})" for m in salida_meds]
    # Aplicamos abs() para enviar los números en positivo a la gráfica
    data_meds = [abs(m['total_despachado']) for m in salida_meds]

    # 2. Tendencia de Despacho (Órdenes Únicas por día)
    # Contamos cuántas órdenes distintas (distinct=True) tuvieron salidas ese día
    tendencia = salidas_kardex.annotate(
        dia=TruncDate('fecha')
    ).values('dia').annotate(
        total=Count('orden_relacionada', distinct=True)
    ).order_by('dia')

    labels_tendencia = [t['dia'].strftime('%d/%m') if hasattr(t['dia'], 'strftime') else t['dia'] for t in tendencia]
    data_tendencia = [t['total'] for t in tendencia]

    return JsonResponse({
        'top_meds': {'labels': labels_meds, 'data': data_meds},
        'tendencia': {'labels': labels_tendencia, 'data': data_tendencia}
    })

@login_required
@rol_requerido(['farmacia'])
def exportar_estadisticas_farmacia(request):
    periodo = request.GET.get('periodo', 'mes')
    hoy = timezone.now().date()

    if periodo == 'semana':
        fecha_inicio = hoy - timedelta(days=7)
    elif periodo == 'ano':
        fecha_inicio = hoy - timedelta(days=365)
    else: 
        fecha_inicio = hoy - timedelta(days=30)

    # 1. Obtenemos los datos del Kardex (Igual que en la API web)
    salidas_kardex = MovimientoInventario.objects.filter(
        tipo_movimiento='SALIDA',
        orden_relacionada__isnull=False,
        fecha__date__gte=fecha_inicio
    )

    salida_meds = salidas_kardex.values(
        nombre_med=F('medicamento__nombre'),
        concentracion=F('medicamento__concentracion')
    ).annotate(
        total_despachado=Sum('cantidad')
    ).order_by('total_despachado')[:5]

    tendencia = salidas_kardex.annotate(
        dia=TruncDate('fecha')
    ).values('dia').annotate(
        total=Count('orden_relacionada', distinct=True)
    ).order_by('dia')

    # 2. Preparamos el archivo Excel en Memoria (Súper rápido, no guarda basura en disco)
    output = io.BytesIO()
    workbook = xlsxwriter.Workbook(output, {'in_memory': True})
    worksheet = workbook.add_worksheet('Reporte Farmacia')

    # 3. Diseños y Colores
    formato_titulo = workbook.add_format({'bold': True, 'font_size': 16, 'font_color': 'white', 'bg_color': '#059669', 'align': 'center', 'valign': 'vcenter'})
    formato_cabecera = workbook.add_format({'bold': True, 'bg_color': '#E5E7EB', 'border': 1})
    formato_celda = workbook.add_format({'border': 1})
    
    # Ajustar ancho de columnas
    worksheet.set_column('A:A', 30)
    worksheet.set_column('B:B', 20)

    # Encabezado Institucional
    worksheet.merge_range('A1:B2', 'Farmacia SIPCRE - Cruz Roja (Boyacá II)', formato_titulo)

    # --- TABLA 1: TOP MEDICAMENTOS ---
    worksheet.write('A4', 'Medicamento', formato_cabecera)
    worksheet.write('B4', 'Unidades Salientes', formato_cabecera)

    fila_actual = 4
    for med in salida_meds:
        nombre_completo = f"{med['nombre_med']} ({med['concentracion']})"
        worksheet.write(fila_actual, 0, nombre_completo, formato_celda)
        worksheet.write(fila_actual, 1, abs(med['total_despachado']), formato_celda)
        fila_actual += 1

    # ¡LA MAGIA! Gráfico de Columnas para Top Medicamentos
    chart_top = workbook.add_chart({'type': 'column'})
    chart_top.add_series({
        'name': 'Unidades Despachadas',
        'categories': ['Reporte Farmacia', 4, 0, fila_actual - 1, 0],
        'values':     ['Reporte Farmacia', 4, 1, fila_actual - 1, 1],
        'fill':       {'color': '#10B981'} # Verde Tailwind
    })
    chart_top.set_title({'name': 'Top 5 Medicamentos con Mayor Salida'})
    chart_top.set_legend({'none': True})
    # Insertamos el gráfico al lado de la tabla (Columna D)
    worksheet.insert_chart('D4', chart_top, {'x_scale': 1.2, 'y_scale': 1.1})

    # --- TABLA 2: TENDENCIA DIARIA ---
    fila_actual += 3 # Dejamos espacio
    fila_inicio_tendencia = fila_actual

    worksheet.write(fila_actual, 0, 'Fecha', formato_cabecera)
    worksheet.write(fila_actual, 1, 'Órdenes Procesadas', formato_cabecera)
    fila_actual += 1

    for t in tendencia:
        fecha_str = t['dia'].strftime('%d/%m/%Y') if hasattr(t['dia'], 'strftime') else str(t['dia'])
        worksheet.write(fila_actual, 0, fecha_str, formato_celda)
        worksheet.write(fila_actual, 1, t['total'], formato_celda)
        fila_actual += 1

    # ¡LA MAGIA! Gráfico de Líneas para Tendencia
    chart_tendencia = workbook.add_chart({'type': 'line'})
    chart_tendencia.add_series({
        'name': 'Órdenes por Día',
        'categories': ['Reporte Farmacia', fila_inicio_tendencia + 1, 0, fila_actual - 1, 0],
        'values':     ['Reporte Farmacia', fila_inicio_tendencia + 1, 1, fila_actual - 1, 1],
        'line':       {'color': '#3B82F6', 'width': 2.5} # Azul Tailwind
    })
    chart_tendencia.set_title({'name': 'Flujo Diario de Despacho'})
    chart_tendencia.set_legend({'none': True})
    # Insertamos el gráfico debajo del otro
    worksheet.insert_chart('D19', chart_tendencia, {'x_scale': 1.2, 'y_scale': 1.1})

    # 4. Cerramos y empaquetamos para enviar
    workbook.close()
    output.seek(0)

    # Configuramos la respuesta HTTP para que el navegador lo descargue como Excel
    response = HttpResponse(
        output.read(), 
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = f'attachment; filename="Estadisticas_Farmacia_{hoy}.xlsx"'
    
    return response

@login_required
@rol_requerido(['farmacia'])
def kardex_farmacia(request):
    query = request.GET.get('q', '')
    fecha_filtro = request.GET.get('fecha', '') # Formato YYYY-MM-DD
    
    # Traemos los movimientos optimizando la consulta
    movimientos_list = MovimientoInventario.objects.select_related('medicamento', 'usuario', 'orden_relacionada').all()
    
    if query:
        movimientos_list = movimientos_list.filter(
            Q(medicamento__nombre__icontains=query) |
            Q(referencia__icontains=query) |
            Q(tipo_movimiento__icontains=query)
        )
        
    if fecha_filtro:
        movimientos_list = movimientos_list.filter(fecha__date=fecha_filtro)
        
    paginator = Paginator(movimientos_list, 10) 
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    return render(request, 'farmacia/kardex.html', {
        'page_obj': page_obj,
        'query': query,
        'fecha_filtro': fecha_filtro
    })

@login_required
@rol_requerido(['farmacia'])
@transaction.atomic
def ajuste_inventario(request):
    if request.method == 'POST':
        tipo_accion = request.POST.get('tipo_accion')
        med_id = request.POST.get('medicamento')
        cantidad = int(request.POST.get('cantidad', 0))
        motivo = request.POST.get('motivo', 'Sin motivo especificado')

        if not med_id or cantidad <= 0:
            messages.error(request, "Debe seleccionar un medicamento principal y una cantidad mayor a cero.")
            return redirect('ajuste_inventario')

        # select_for_update: los ajustes compiten con despachos y ventas por el
        # mismo stock; el lock evita descuadres si ocurren a la vez.
        medicamento = get_object_or_404(Medicamento.objects.select_for_update(), id=med_id)

        try:
            if tipo_accion == 'devolucion':
                stock_antes = medicamento.stock_actual
                medicamento.stock_actual += cantidad
                medicamento.save()
                # Reposición de lotes (orden FEFO inverso: rellena lo drenado primero)
                reintegrar_lotes(medicamento, cantidad)
                MovimientoInventario.objects.create(
                    medicamento=medicamento, tipo_movimiento='DEVOLUCION',
                    cantidad=cantidad, stock_resultante=medicamento.stock_actual,
                    usuario=request.user, referencia=f"Devolución manual: {motivo}"
                )
                # Auditoría si es controlado — registramos la devolución también
                if medicamento.es_controlado:
                    AuditoriaControlado.objects.create(
                        medicamento=medicamento,
                        usuario_despacho=request.user,
                        orden=None,  # No hay orden en ajuste manual
                        nombre_paciente='N/A',
                        cedula_paciente='N/A',
                        cantidad_despachada=cantidad,
                        stock_antes=stock_antes,
                        stock_despues=medicamento.stock_actual,
                        ip_origen=request.META.get('REMOTE_ADDR'),
                        observacion=f"Devolución manual: {motivo}"
                    )
                    logger.warning(f"CONTROLADO AJUSTE | tipo=DEVOLUCION | medicamento={medicamento.nombre} | cantidad={cantidad} | usuario={request.user.email} | motivo={motivo}")
                messages.success(request, f"Se reintegraron {cantidad} uds de {medicamento.nombre} al inventario.")

            elif tipo_accion == 'merma':
                if cantidad > medicamento.stock_actual:
                    messages.error(request, f"Error: No puede dar de baja más unidades de las que existen en stock ({medicamento.stock_actual}).")
                    return redirect('ajuste_inventario')
                
                stock_antes = medicamento.stock_actual
                medicamento.stock_actual -= cantidad
                medicamento.save()
                # Consumo de lotes por vencimiento más próximo (FEFO)
                descontar_lotes_fefo(medicamento, cantidad)
                MovimientoInventario.objects.create(
                    medicamento=medicamento, tipo_movimiento='AJUSTE',
                    cantidad=-cantidad, stock_resultante=medicamento.stock_actual,
                    usuario=request.user, referencia=f"Merma/Dañado: {motivo}"
                )
                # Auditoría si es controlado — una merma de controlado es crítica
                if medicamento.es_controlado:
                    AuditoriaControlado.objects.create(
                        medicamento=medicamento,
                        usuario_despacho=request.user,
                        orden=None,
                        nombre_paciente='N/A',
                        cedula_paciente='N/A',
                        cantidad_despachada=cantidad,
                        stock_antes=stock_antes,
                        stock_despues=medicamento.stock_actual,
                        ip_origen=request.META.get('REMOTE_ADDR'),
                        observacion=f"Merma/Dañado: {motivo}"
                    )
                    logger.warning(f"CONTROLADO AJUSTE | tipo=MERMA | medicamento={medicamento.nombre} | cantidad={cantidad} | usuario={request.user.email} | motivo={motivo}")
                messages.warning(request, f"Se dio de baja {cantidad} uds de {medicamento.nombre} por merma.")

            elif tipo_accion == 'cambio':
                med_nuevo_id = request.POST.get('medicamento_nuevo')
                if not med_nuevo_id:
                    messages.error(request, "Para un cambio, debe seleccionar el medicamento que va a entregar.")
                    return redirect('ajuste_inventario')
                    
                medicamento_nuevo = get_object_or_404(Medicamento.objects.select_for_update(), id=med_nuevo_id)
                
                if cantidad > medicamento_nuevo.stock_actual:
                    messages.error(request, f"Stock insuficiente: Solo hay {medicamento_nuevo.stock_actual} uds de {medicamento_nuevo.nombre} para realizar el cambio.")
                    return redirect('ajuste_inventario')

                # A. Reintegro del que devuelven
                stock_antes_devuelto = medicamento.stock_actual
                medicamento.stock_actual += cantidad
                medicamento.save()
                reintegrar_lotes(medicamento, cantidad)
                MovimientoInventario.objects.create(
                    medicamento=medicamento, tipo_movimiento='DEVOLUCION',
                    cantidad=cantidad, stock_resultante=medicamento.stock_actual,
                    usuario=request.user, referencia=f"Cambio (Reintegro): {motivo}"
                )
                if medicamento.es_controlado:
                    AuditoriaControlado.objects.create(
                        medicamento=medicamento,
                        usuario_despacho=request.user,
                        orden=None,
                        nombre_paciente='N/A',
                        cedula_paciente='N/A',
                        cantidad_despachada=cantidad,
                        stock_antes=stock_antes_devuelto,
                        stock_despues=medicamento.stock_actual,
                        ip_origen=request.META.get('REMOTE_ADDR'),
                        observacion=f"Cambio (Reintegro): {motivo}"
                    )
                    logger.warning(f"CONTROLADO AJUSTE | tipo=REINTEGRO | medicamento={medicamento.nombre} | cantidad={cantidad} | usuario={request.user.email} | motivo={motivo}")

                # B. Salida del nuevo que entregamos
                stock_antes_nuevo = medicamento_nuevo.stock_actual
                medicamento_nuevo.stock_actual -= cantidad
                medicamento_nuevo.save()
                descontar_lotes_fefo(medicamento_nuevo, cantidad)
                MovimientoInventario.objects.create(
                    medicamento=medicamento_nuevo, tipo_movimiento='SALIDA',
                    cantidad=-cantidad, stock_resultante=medicamento_nuevo.stock_actual,
                    usuario=request.user, referencia=f"Cambio (Entrega): Reemplaza a {medicamento.nombre}"
                )
                if medicamento_nuevo.es_controlado:
                    AuditoriaControlado.objects.create(
                        medicamento=medicamento_nuevo,
                        usuario_despacho=request.user,
                        orden=None,
                        nombre_paciente='N/A',
                        cedula_paciente='N/A',
                        cantidad_despachada=cantidad,
                        stock_antes=stock_antes_nuevo,
                        stock_despues=medicamento_nuevo.stock_actual,
                        ip_origen=request.META.get('REMOTE_ADDR'),
                        observacion=f"Cambio (Entrega): Reemplaza a {medicamento.nombre}. {motivo}"
                    )
                    logger.warning(f"CONTROLADO AJUSTE | tipo=CAMBIO_ENTREGA | medicamento={medicamento_nuevo.nombre} | cantidad={cantidad} | usuario={request.user.email} | motivo={motivo}")
                messages.success(request, f"Cambio exitoso: Reintegrado {medicamento.nombre} | Entregado {medicamento_nuevo.nombre}.")

            return redirect('kardex_farmacia')

        except Exception as e:
            messages.error(request, f"Ocurrió un error al procesar el ajuste: {str(e)}")
            return redirect('ajuste_inventario')

    medicamentos = Medicamento.objects.all().order_by('nombre')
    return render(request, 'farmacia/ajuste_inventario.html', {'medicamentos': medicamentos})

@login_required
@rol_requerido(['farmacia'])
def gestion_lotes(request):
    query = request.GET.get('q', '')
    hoy = timezone.now().date()
    alerta_fecha = hoy + timedelta(days=60) # Alerta amarilla si vence en 60 días o menos

    # Traemos solo los lotes que aún tienen inventario físico
    lotes_list = LoteMedicamento.objects.filter(cantidad_actual__gt=0).select_related('medicamento').order_by('fecha_vencimiento')

    if query:
        lotes_list = lotes_list.filter(
            Q(medicamento__nombre__icontains=query) |
            Q(numero_lote__icontains=query)
        )

    paginator = Paginator(lotes_list, 12) # 12 recuadros por página (Grid de 3 o 4 columnas)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    context = {
        'page_obj': page_obj,
        'query': query,
        'hoy': hoy,
        'alerta_fecha': alerta_fecha
    }
    return render(request, 'farmacia/gestion_lotes.html', context)

@login_required
@rol_requerido(['farmacia'])
@require_POST
def dar_baja_lote(request, lote_id):
    """
    Da de baja un lote por vencimiento: descuenta sus unidades del stock real
    del medicamento y deja constancia en el kardex como merma (AJUSTE). El
    producto vencido no se vende — se descarta de forma trazable.
    """
    with transaction.atomic():
        lote = get_object_or_404(LoteMedicamento.objects.select_related('medicamento'), id=lote_id)
        medicamento = Medicamento.objects.select_for_update().get(pk=lote.medicamento_id)

        cantidad_baja = lote.cantidad_actual
        if cantidad_baja <= 0:
            messages.info(request, f"El lote {lote.numero_lote} de {medicamento.nombre} ya no tiene unidades.")
            return redirect('gestion_lotes')

        # Descontar del stock real y vaciar el lote
        medicamento.stock_actual = max(0, medicamento.stock_actual - cantidad_baja)
        medicamento.save(update_fields=['stock_actual'])

        lote.cantidad_actual = 0
        lote.save(update_fields=['cantidad_actual'])

        MovimientoInventario.objects.create(
            medicamento=medicamento,
            tipo_movimiento='AJUSTE',
            cantidad=-cantidad_baja,
            stock_resultante=medicamento.stock_actual,
            usuario=request.user,
            referencia=f"Baja por vencimiento — Lote {lote.numero_lote} (venció {lote.fecha_vencimiento.strftime('%d/%m/%Y')})"
        )

    messages.success(request, f"Lote {lote.numero_lote} de {medicamento.nombre} dado de baja. Se retiraron {cantidad_baja} unidades vencidas del inventario.")
    return redirect('gestion_lotes')

@login_required
@rol_requerido(['farmacia'])
@require_POST
def editar_fecha_lote(request, lote_id):
    """
    Corrige la fecha de vencimiento de un lote (por un error de carga). No
    mueve stock; solo actualiza la fecha. Útil tanto para arreglar un tecleo
    errado como para definir la fecha de los lotes iniciales creados con
    placeholder.
    """
    lote = get_object_or_404(LoteMedicamento.objects.select_related('medicamento'), id=lote_id)
    nueva_fecha = (request.POST.get('fecha_vencimiento') or '').strip()

    if not nueva_fecha:
        messages.error(request, "Debe indicar una fecha de vencimiento válida.")
        return redirect('gestion_lotes')

    try:
        fecha_parseada = datetime.strptime(nueva_fecha, '%Y-%m-%d').date()
    except ValueError:
        messages.error(request, "El formato de la fecha no es válido.")
        return redirect('gestion_lotes')

    lote.fecha_vencimiento = fecha_parseada
    lote.save(update_fields=['fecha_vencimiento'])

    messages.success(request, f"Fecha de vencimiento del Lote {lote.numero_lote} ({lote.medicamento.nombre}) actualizada a {fecha_parseada.strftime('%d/%m/%Y')}.")
    return redirect('gestion_lotes')

@login_required
@rol_requerido(['farmacia'])
def requisicion_compra(request):
    # Buscamos SOLO los medicamentos cuyo stock actual es menor o igual al mínimo
    medicamentos_criticos = Medicamento.objects.filter(stock_actual__lte=F('stock_minimo')).order_by('nombre')
    
    context = {
        'medicamentos': medicamentos_criticos,
        'fecha_emision': timezone.now(),
        'farmaceuta': request.user,
    }
    
    # Nota: Usamos un template especial para impresión, sin menús de navegación
    return render(request, 'farmacia/requisicion_compra.html', context)

@login_required
@rol_requerido(['farmacia'])
def caja_farmacia(request):
    if request.method == 'POST':
        try:
            datos = json.loads(request.body)
            paciente_nombre = (datos.get('paciente_nombre') or '').strip()
            paciente_cedula = normalizar_cedula(datos.get('paciente_cedula'))
            validacion_psicotropicos = datos.get('validacion_psicotropicos', False)
            carrito = datos.get('carrito', [])
            # Método de cobro simulado: solo dual ($ o Bs). Se normaliza a una
            # de las dos etiquetas y queda reflejado en el kardex.
            metodo_pago_raw = (datos.get('metodo_pago') or 'USD').upper()
            metodo_pago = 'Bs' if metodo_pago_raw in ('BS', 'BOLIVARES', 'BOLÍVARES') else '$'
 
            if not carrito:
                return JsonResponse({'success': False, 'error': 'El carrito está vacío.'})
 
            # Requisito: no se concreta una venta sin identificar al comprador.
            if not paciente_nombre or not paciente_cedula:
                return JsonResponse({'success': False, 'error': 'Debe registrar el nombre y la cédula del comprador para concretar la venta.'})
            if not cedula_es_valida(paciente_cedula):
                return JsonResponse({'success': False, 'error': 'La cédula del comprador no es válida (numérica, máx. 40.000.000).'})

            with transaction.atomic():
                orden = OrdenFarmacia.objects.create(
                    nombre_paciente=paciente_nombre,
                    cedula_paciente=paciente_cedula,
                    estado='COMPLETADA',
                )

                for item in carrito:
                    med = Medicamento.objects.select_for_update().get(id=item['id'])
                    cant = int(item['cantidad'])

                    if cant > med.stock_actual:
                        raise ReglaNegocioError(f"Stock insuficiente para {med.nombre}. Solo quedan {med.stock_actual}.")
                    
                    if med.es_controlado and not validacion_psicotropicos:
                        raise ReglaNegocioError(f"Falta validación física del récipe para el psicotrópico: {med.nombre}")

                    stock_antes = med.stock_actual  # ← guardar antes de restar
                    med.stock_actual -= cant
                    med.save()
                    # Consumo de lotes por vencimiento más próximo (FEFO)
                    descontar_lotes_fefo(med, cant)

                    # Auditoría para controlados
                    if med.es_controlado:
                        AuditoriaControlado.objects.create(
                            medicamento=med,
                            usuario_despacho=request.user,
                            orden=orden,
                            nombre_paciente=paciente_nombre,
                            cedula_paciente=paciente_cedula,
                            cantidad_despachada=cant,
                            stock_antes=stock_antes,
                            stock_despues=med.stock_actual,
                            ip_origen=request.META.get('REMOTE_ADDR'),
                            observacion='Venta directa en caja farmacia'
                        )
                        logger.warning(f"CONTROLADO DESPACHADO | medicamento={med.nombre} | cantidad={cant} | paciente={paciente_cedula} | usuario={request.user.email} | orden={orden.id}")

                    DetalleDespacho.objects.create(
                        orden=orden,
                        medicamento=med,
                        cantidad=cant,
                        precio_unitario=med.precio
                    )

                    # Monto de este ítem (Decimal, no float) para reflejarlo en el kardex.
                    precio_unit = med.precio if med.precio else Decimal('0.00')
                    subtotal_item = precio_unit * cant

                    MovimientoInventario.objects.create(
                        medicamento=med,
                        tipo_movimiento='SALIDA',
                        cantidad=-cant,
                        stock_resultante=med.stock_actual,
                        usuario=request.user,
                        referencia=f"Venta Directa | ${subtotal_item:.2f} | Pago: {metodo_pago} | Orden #{orden.id}",
                        orden_relacionada=orden
                    )

            return JsonResponse({'success': True, 'orden_id': orden.id})

        except ReglaNegocioError as e:
            # Mensaje de regla de negocio: es seguro y útil mostrarlo al usuario.
            return JsonResponse({'success': False, 'error': str(e)})
        except Exception as e:
            # Error inesperado (bug, fallo de BD, payload corrupto): se registra
            # con traza completa en el log y al cliente solo se le da un mensaje
            # genérico, para no filtrar detalles internos del sistema.
            logger.error(f"Error inesperado en caja_farmacia: {e}", exc_info=True)
            return JsonResponse({'success': False, 'error': 'Ocurrió un error al procesar la venta. Intenta de nuevo.'})

    medicamentos_raw = Medicamento.objects.filter(stock_actual__gt=0).values(
        'id', 'nombre', 'concentracion', 'precio', 'stock_actual', 'codigo_barras', 'es_controlado'
    )
    # Tasa BCV para el cobro dual ($/Bs), misma fuente que la Caja Central.
    # Si la API no responde, se usa la última tasa registrada en una sesión de
    # caja (la más cercana a la realidad); si nunca hubo, un respaldo mínimo.
    tasa_bcv = obtener_tasa_bcv()
    if not tasa_bcv:
        ultima_sesion = SesionCaja.objects.order_by('-fecha_apertura').first()
        tasa_bcv = ultima_sesion.tasa_bcv_dia if ultima_sesion else Decimal('0.00')

    # Se pasa la LISTA cruda (no un string ya serializado): el filtro
    # json_script del template se encarga de serializar y escapar de forma
    # segura, eliminando el |safe que permitía romper el <script> con un
    # nombre de medicamento malicioso.
    return render(request, 'farmacia/caja.html', {
        'medicamentos': list(medicamentos_raw),
        'tasa_bcv': float(tasa_bcv),
    })

@login_required
@rol_requerido(['farmacia'])
def analizar_imagen_medicamento(request):
    if request.method == 'POST':
        try:
            from google import genai
            from google.genai import types
            API_KEY = os.getenv("GEMINI_API_KEY")
            if not API_KEY:
                raise ValueError("¡Falta la GEMINI_API_KEY en el archivo .env!")
            
            # 1. Instanciamos el nuevo cliente
            client = genai.Client(api_key=API_KEY)

            # Captura de imagen desde el frontend
            data = json.loads(request.body)
            imagen_base64 = data.get('imagen')
            
            if not imagen_base64:
                return JsonResponse({'success': False, 'error': 'No se recibió ninguna imagen.'})

            # Separamos el encabezado (data:image/jpeg;base64) del contenido
            formato, imgstr = imagen_base64.split(';base64,')
            mime_type = formato.split(':')[-1]
            
            # 2. Decodificamos el string a bytes (Requisito del nuevo SDK)
            image_bytes = base64.b64decode(imgstr)
            
            # 3. Preparamos la imagen usando el nuevo formato Part.from_bytes
            imagen_data = types.Part.from_bytes(
                data=image_bytes,
                mime_type=mime_type
            )

            prompt = """
            Eres un asistente experto en farmacia. Analiza esta imagen de un medicamento.
            Extrae la siguiente información y devuélvela ESTRICTAMENTE en un formato JSON válido, sin texto adicional, sin formato markdown, solo las llaves { y }.
            Si no encuentras un dato, pon un string vacío "".
            
            Estructura requerida:
            {
                "nombre": "Nombre del medicamento (ej. Losartán Potásico)",
                "concentracion": "Concentración (ej. 50mg, 120ml)",
                "presentacion": "Forma farmacéutica (ej. Tabletas, Jarabe, Suspensión)",
                "laboratorio": "Nombre del laboratorio fabricante si está visible"
            }
            """

            # 4. Hacemos la petición con el nuevo cliente y forzamos salida JSON nativa
            response = client.models.generate_content(
                model='gemini-3.5-flash',
                contents=[prompt, imagen_data],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                )
            )
            
            # Limpiamos la respuesta por precaución (aunque el config de arriba ya lo garantiza)
            texto_limpio = response.text.replace('```json', '').replace('```', '').strip()
            
            # Convertimos el texto de la IA a un diccionario de Python
            datos_extraidos = json.loads(texto_limpio)
            
            return JsonResponse({'success': True, 'datos': datos_extraidos})

        except Exception as e:
            logger.error(f"Error al analizar imagen de medicamento: {e}")
            return JsonResponse({
                'success': False,
                'error': 'No se pudo analizar la imagen. Verifica que sea una foto clara del medicamento e intentalo de nuevo.'
            })

    return JsonResponse({'success': False, 'error': 'Método no permitido.'})

