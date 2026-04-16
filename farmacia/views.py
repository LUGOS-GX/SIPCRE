from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponse
from django.contrib import messages
from django.db.models import F, Q, Sum, Count
from django.db.models.functions import TruncDate
from django.db import transaction
from django.core.paginator import Paginator
from django.utils import timezone
from datetime import timedelta
from .models import OrdenFarmacia, Medicamento, DetalleDespacho, LoteMedicamento, MovimientoInventario
from administracion.models import Factura, DetalleFactura
from .forms import MedicamentoForm, LoteMedicamentoForm
from usuarios.decorators import rol_requerido
import json
import io
import xlsxwriter

@login_required
@rol_requerido(['farmacia'])
def dashboard_farmacia(request):
    # --- 1. CAPTURAR LA BÚSQUEDA ---
    query = request.GET.get('q', '')
    
    # 2. Base de datos: Órdenes pendientes
    ordenes_pendientes_list = OrdenFarmacia.objects.filter(estado='Pendiente').order_by('-fecha_solicitud')
    
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
    ordenes_despachadas = OrdenFarmacia.objects.filter(estado='Despachado').order_by('-fecha_despacho')[:15]
    
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
    
    # Seguridad: Si ya se despachó, no dejamos que entren a modificarla
    if orden.estado != 'Pendiente':
        messages.warning(request, "Esta orden ya fue procesada o cancelada.")
        return redirect('dashboard_farmacia')

    # Traemos solo los medicamentos que tengan al menos 1 unidad en stock
    medicamentos_disponibles = Medicamento.objects.filter(stock_actual__gt=0).order_by('nombre')
    detalles = orden.detalles.all() # Lo que ya se ha agregado a la bolsa del paciente

    if request.method == 'POST':
        accion = request.POST.get('accion')

        # Si el farmaceuta hace clic en "Agregar a la Bolsa"
        if accion == 'agregar_item':
            med_id = request.POST.get('medicamento_id')
            cantidad = int(request.POST.get('cantidad', 0))
            
            if med_id and cantidad > 0:
                medicamento = get_object_or_404(Medicamento, id=med_id)
                
                # Control de psicotrópicos
                if getattr(medicamento, 'es_controlado', False): 
                    
                    # --- NUEVA REGLA: BLOQUEO A MÉDICO GENERAL ---
                    # Verificamos si la orden viene de un médico interno y leemos su especialidad
                    if orden.medico and getattr(orden.medico, 'especialidad', ''):
                        especialidad_medico = orden.medico.especialidad.lower()
                        if 'general' in especialidad_medico:
                            messages.error(request, f"⛔ Bloqueo de Seguridad: No se permite despachar el psicotrópico '{medicamento.nombre}' bajo la indicación de un Médico General.")
                            return redirect('despachar_orden', orden_id=orden.id)
                    # ---------------------------------------------
                    
                    # Si NO hay un paciente registrado en el sistema (es paciente externo/de paso)
                    if not orden.paciente:
                        validacion_firma = request.POST.get('validacion_firma')
                        if not validacion_firma:
                            messages.error(request, f"ALERTA SEGURIDAD: {medicamento.nombre} es un psicotrópico. Para récipes externos DEBE confirmar físicamente el sello y firma del médico.")
                            return redirect('despachar_orden', orden_id=orden.id)
                    # Si hay paciente (interno), la firma digital del médico del sistema lo avala y pasa directo.

                # Verificamos que no intenten sacar más de lo que hay
                if cantidad <= medicamento.stock_actual:
                    # 1. Creamos el registro de entrega
                    DetalleDespacho.objects.create(
                        orden=orden,
                        medicamento=medicamento,
                        cantidad=cantidad,
                        precio_unitario=medicamento.precio
                    )
                    # 2. MAGIA: Descontamos del inventario físico
                    medicamento.stock_actual -= cantidad
                    medicamento.save()

                    MovimientoInventario.objects.create(
                        medicamento=medicamento,
                        tipo_movimiento='SALIDA',
                        cantidad=-cantidad, # Negativo porque es salida
                        stock_resultante=medicamento.stock_actual,
                        usuario=request.user,
                        referencia=f"Despacho Orden #{orden.id}",
                        orden_relacionada=orden
                    )
                    messages.success(request, f"Se agregaron {cantidad}x {medicamento.nombre} a la orden.")
                else:
                    messages.error(request, f"¡Stock insuficiente! Solo quedan {medicamento.stock_actual} unidades de {medicamento.nombre}.")
        
        # Si el farmaceuta hace clic en "Finalizar y Entregar"
        elif accion == 'finalizar_despacho':
            orden.estado = 'Despachado'
            orden.fecha_despacho = timezone.now()
            orden.save()
            
            # --- MAGIA ACTUALIZADA: FACTURAS PARA TODOS ---
            if orden.paciente:
                # Paciente formal registrado en Recepción
                nueva_factura = Factura.objects.create(paciente=orden.paciente, estado='Pendiente')
            else:
                # Paciente de paso (Solo venía por el récipe)
                nueva_factura = Factura.objects.create(
                    nombre_cliente=orden.nombre_paciente,
                    cedula_cliente=orden.cedula_paciente,
                    estado='Pendiente'
                )
                
            # Metemos los medicamentos a la factura
            for detalle in detalles:
                DetalleFactura.objects.create(
                    factura=nueva_factura,
                    departamento='Farmacia',
                    descripcion=f"Med: {detalle.medicamento.nombre} {detalle.medicamento.concentracion}",
                    cantidad=detalle.cantidad,
                    precio_unitario=detalle.precio_unitario
                )
                
            messages.success(request, f"Orden #{orden.id} despachada. La factura fue enviada a Caja Central.")
            return redirect('dashboard_farmacia')

    # Calculamos el total de la factura (sumando los subtotales de la bolsa)
    total_factura = sum(detalle.subtotal() for detalle in detalles)

    context = {
        'orden': orden,
        'medicamentos_disponibles': medicamentos_disponibles,
        'detalles': detalles,
        'total_factura': total_factura
    }
    return render(request, 'farmacia/despachar_orden.html', context)

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
            # 1. Guardamos el lote
            nuevo_lote = form.save(commit=False)
            nuevo_lote.cantidad_actual = nuevo_lote.cantidad_ingresada
            nuevo_lote.save()
            
            # 2. ACTUALIZAMOS EL STOCK DEL MEDICAMENTO PRINCIPAL
            medicamento = nuevo_lote.medicamento
            medicamento.stock_actual += nuevo_lote.cantidad_ingresada
            medicamento.save()
            
            # 3. REGISTRAMOS EL MOVIMIENTO EN EL KARDEX
            MovimientoInventario.objects.create(
                medicamento=medicamento,
                tipo_movimiento='ENTRADA',
                cantidad=nuevo_lote.cantidad_ingresada,
                stock_resultante=medicamento.stock_actual,
                usuario=request.user,
                referencia=f"Ingreso de Lote #{nuevo_lote.numero_lote}"
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
def ajuste_inventario(request):
    if request.method == 'POST':
        tipo_accion = request.POST.get('tipo_accion')
        med_id = request.POST.get('medicamento')
        cantidad = int(request.POST.get('cantidad', 0))
        motivo = request.POST.get('motivo', 'Sin motivo especificado')

        if not med_id or cantidad <= 0:
            messages.error(request, "Debe seleccionar un medicamento principal y una cantidad mayor a cero.")
            return redirect('ajuste_inventario')

        medicamento = get_object_or_404(Medicamento, id=med_id)

        try:
            # 1. DEVOLUCIÓN (Suma al stock)
            if tipo_accion == 'devolucion':
                medicamento.stock_actual += cantidad
                medicamento.save()
                MovimientoInventario.objects.create(
                    medicamento=medicamento, tipo_movimiento='DEVOLUCION',
                    cantidad=cantidad, stock_resultante=medicamento.stock_actual,
                    usuario=request.user, referencia=f"Devolución manual: {motivo}"
                )
                messages.success(request, f"Se reintegraron {cantidad} uds de {medicamento.nombre} al inventario.")

            # 2. MERMA / DAÑO (Resta del stock)
            elif tipo_accion == 'merma':
                if cantidad > medicamento.stock_actual:
                    messages.error(request, f"Error: No puede dar de baja más unidades de las que existen en stock ({medicamento.stock_actual}).")
                    return redirect('ajuste_inventario')
                
                medicamento.stock_actual -= cantidad
                medicamento.save()
                MovimientoInventario.objects.create(
                    medicamento=medicamento, tipo_movimiento='AJUSTE',
                    cantidad=-cantidad, stock_resultante=medicamento.stock_actual,
                    usuario=request.user, referencia=f"Merma/Dañado: {motivo}"
                )
                messages.warning(request, f"Se dio de baja {cantidad} uds de {medicamento.nombre} por merma.")

            # 3. CAMBIO POR OTRO MEDICAMENTO (Suma el devuelto, Resta el entregado)
            elif tipo_accion == 'cambio':
                med_nuevo_id = request.POST.get('medicamento_nuevo')
                if not med_nuevo_id:
                    messages.error(request, "Para un cambio, debe seleccionar el medicamento que va a entregar.")
                    return redirect('ajuste_inventario')
                    
                medicamento_nuevo = get_object_or_404(Medicamento, id=med_nuevo_id)
                
                if cantidad > medicamento_nuevo.stock_actual:
                    messages.error(request, f"Stock insuficiente: Solo hay {medicamento_nuevo.stock_actual} uds de {medicamento_nuevo.nombre} para realizar el cambio.")
                    return redirect('ajuste_inventario')

                # A. Reintegro del que devuelven
                medicamento.stock_actual += cantidad
                medicamento.save()
                MovimientoInventario.objects.create(
                    medicamento=medicamento, tipo_movimiento='DEVOLUCION',
                    cantidad=cantidad, stock_resultante=medicamento.stock_actual,
                    usuario=request.user, referencia=f"Cambio (Reintegro): {motivo}"
                )
                
                # B. Salida del nuevo que entregamos
                medicamento_nuevo.stock_actual -= cantidad
                medicamento_nuevo.save()
                MovimientoInventario.objects.create(
                    medicamento=medicamento_nuevo, tipo_movimiento='SALIDA',
                    cantidad=-cantidad, stock_resultante=medicamento_nuevo.stock_actual,
                    usuario=request.user, referencia=f"Cambio (Entrega): Reemplaza a {medicamento.nombre}"
                )
                messages.success(request, f"Cambio exitoso: Reintegrado {medicamento.nombre} | Entregado {medicamento_nuevo.nombre}.")

            return redirect('kardex_farmacia')

        except Exception as e:
            messages.error(request, f"Ocurrió un error al procesar el ajuste: {str(e)}")
            return redirect('ajuste_inventario')

    # Para cargar la vista GET
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
            # Capturamos el carrito enviado por JavaScript
            datos = json.loads(request.body)
            paciente_nombre = datos.get('paciente_nombre', 'Paciente de Paso')
            paciente_cedula = datos.get('paciente_cedula', 'S/N')
            validacion_psicotropicos = datos.get('validacion_psicotropicos', False)
            carrito = datos.get('carrito', [])

            if not carrito:
                return JsonResponse({'success': False, 'error': 'El carrito está vacío.'})

            # Usamos transacciones para que si ocurre un error, no se guarde nada a medias
            with transaction.atomic():
                # 1. Creamos la orden "Express" (Estado Completada automáticamente)
                orden = OrdenFarmacia.objects.create(
                    nombre_paciente=paciente_nombre,
                    cedula_paciente=paciente_cedula,
                    estado='COMPLETADA',
                    # No asignamos paciente ni medico porque es venta directa de paso
                )

                # 2. Procesamos cada artículo
                for item in carrito:
                    med = Medicamento.objects.select_for_update().get(id=item['id'])
                    cant = int(item['cantidad'])

                    if cant > med.stock_actual:
                        raise Exception(f"Stock insuficiente para {med.nombre}. Solo quedan {med.stock_actual}.")
                    
                    if med.es_controlado and not validacion_psicotropicos:
                        raise Exception(f"Falta validación física del récipe para el psicotrópico: {med.nombre}")

                    # Descontamos stock
                    med.stock_actual -= cant
                    med.save()

                    # Creamos el detalle de la factura
                    DetalleDespacho.objects.create(
                        orden=orden,
                        medicamento=med,
                        cantidad=cant,
                        precio_unitario=med.precio
                    )

                    # Escribimos en el Kardex
                    MovimientoInventario.objects.create(
                        medicamento=med,
                        tipo_movimiento='SALIDA',
                        cantidad=-cant,
                        stock_resultante=med.stock_actual,
                        usuario=request.user,
                        referencia=f"Venta en Caja Directa. Orden #{orden.id}",
                        orden_relacionada=orden
                    )

            return JsonResponse({'success': True, 'orden_id': orden.id})

        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})

    # GET: Preparamos el inventario para enviarlo a JavaScript y que el escaneo sea instantáneo
    # Convertimos los datos a un formato que JS entienda fácilmente
    medicamentos_raw = Medicamento.objects.filter(stock_actual__gt=0).values(
        'id', 'nombre', 'concentracion', 'precio', 'stock_actual', 'codigo_barras', 'es_controlado'
    )
    medicamentos_json = json.dumps(list(medicamentos_raw), default=str)

    return render(request, 'farmacia/caja.html', {'medicamentos_json': medicamentos_json})

@login_required
@rol_requerido(['farmacia'])
def analizar_imagen_medicamento(request):
    if request.method == 'POST':
        try:
            import google.generativeai as genai
            import os
            API_KEY = os.getenv("GEMINI_API_KEY")
            if not API_KEY:
                raise ValueError("¡Falta la GEMINI_API_KEY en el archivo .env!")
            genai.configure(api_key=API_KEY)

            #captura img
            data = json.loads(request.body)
            imagen_base64 = data.get('imagen')
            
            if not imagen_base64:
                return JsonResponse({'success': False, 'error': 'No se recibió ninguna imagen.'})

            formato, imgstr = imagen_base64.split(';base64,')
            
            model = genai.GenerativeModel('gemini-2.5-flash')
            
            imagen_data = {
                "mime_type": formato.split(':')[-1],
                "data": imgstr
            }

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

            # Hacemos la petición a la IA
            response = model.generate_content([prompt, imagen_data])
            
            # Limpiamos la respuesta por si la IA añade markdown de código (```json ... ```)
            texto_limpio = response.text.replace('```json', '').replace('```', '').strip()
            
            # Convertimos el texto de la IA a un diccionario de Python
            datos_extraidos = json.loads(texto_limpio)
            
            return JsonResponse({'success': True, 'datos': datos_extraidos})

        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
            
    return JsonResponse({'success': False, 'error': 'Método no permitido.'})

