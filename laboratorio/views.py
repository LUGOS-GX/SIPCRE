from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponse
from django.contrib import messages
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from django.conf import settings
from django.views.decorators.http import require_POST
from datetime import timedelta
from django.utils import timezone
from django.core.paginator import Paginator
from collections import Counter
from django.db.models import Q, Count
from django.db.models.functions import TruncDate
from .models import SolicitudExamen, ExamenCatalogo, ResultadoDetalle
from farmacia.models import Medicamento, MovimientoInventario
from usuarios.forms import RegistroLaboratorioForm
from usuarios.decorators import rol_requerido
from io import BytesIO
from xhtml2pdf import pisa
from django.template.loader import get_template
from django.core.files.base import ContentFile
import io
import xlsxwriter
import threading

@login_required
@rol_requerido(['laboratorio'])
def dashboard_lab(request):
    query = request.GET.get('q', '').strip()
    tab_activa = request.GET.get('tab', 'activas')
    fecha_filtro = request.GET.get('fecha', '').strip()

    # 1. Consultas Base
    ordenes_activas = SolicitudExamen.objects.filter(estado__in=['Pendiente', 'Procesando']).order_by('-fecha_solicitud')
    ordenes_historial = SolicitudExamen.objects.filter(estado='Realizado').order_by('-fecha_resultado')
    
    pendientes_count = ordenes_activas.filter(estado='Pendiente').count()

    # 2. Aplicar Búsqueda Inteligente (Texto)
    if query:
        ordenes_activas = ordenes_activas.filter(
            Q(paciente__nombres__icontains=query) |
            Q(paciente__cedula__icontains=query) |
            Q(nombre_paciente__icontains=query) |
            Q(cedula_paciente__icontains=query) |
            Q(id__icontains=query)
        )
        ordenes_historial = ordenes_historial.filter(
            Q(paciente__nombres__icontains=query) |
            Q(paciente__cedula__icontains=query) |
            Q(nombre_paciente__icontains=query) |
            Q(cedula_paciente__icontains=query) |
            Q(id__icontains=query)
        )

    # 3. NUEVO: Filtro por Fecha (Solo afecta al historial)
    if fecha_filtro:
        ordenes_historial = ordenes_historial.filter(fecha_resultado__date=fecha_filtro)

    # 4. Paginación
    paginator_activas = Paginator(ordenes_activas, 10)
    page_number_activas = request.GET.get('page_activas')
    page_activas = paginator_activas.get_page(page_number_activas)

    paginator_historial = Paginator(ordenes_historial, 10)
    page_number_historial = request.GET.get('page_historial')
    page_historial = paginator_historial.get_page(page_number_historial)

    context = {
        'page_activas': page_activas,
        'page_historial': page_historial,
        'pendientes_count': pendientes_count,
        'query': query,
        'fecha_filtro': fecha_filtro, # Enviamos la fecha al template
        'tab_activa': tab_activa,
    }
    return render(request, 'laboratorio/dashboard_lab.html', context)

def enviar_correo_resultados_async(orden_id, pdf_content):
    """
    Función optimizada para evitar archivos corruptos y manejar el peso.
    Usa el template: laboratorio/correo_px_presencial.html
    """
    try:
        orden = SolicitudExamen.objects.get(id=orden_id)
        
        correo_destino = None
        if orden.correo_paciente:
            correo_destino = orden.correo_paciente
        elif orden.paciente and hasattr(orden.paciente, 'usuario') and orden.paciente.usuario.email:
            correo_destino = orden.paciente.usuario.email
            
        if not correo_destino:
            return 

        nombre_paciente = orden.nombre_paciente if orden.nombre_paciente else orden.paciente.nombres

        context = {
            'nombre_paciente': nombre_paciente,
            'orden_id': orden.id,
            'fecha_resultado': orden.fecha_resultado,
            'medico_nombre': orden.medico.usuario.last_name,
        }
        
        subject = f'Resultados de Laboratorio Listos - Orden #{orden.id:05d}'
        from_email = settings.DEFAULT_FROM_EMAIL
        
        # Límite de 15MB para adjuntos (considerando el aumento por codificación Base64)
        LIMITE_BYTES = 15728640 
        peso_archivo = len(pdf_content)

        if peso_archivo > LIMITE_BYTES:
            # Plantilla para retiro presencial (nombre corregido)
            html_content = render_to_string('laboratorio/correo_px_presencial.html', context)
            text_content = strip_tags(html_content)
            
            msg = EmailMultiAlternatives(subject, text_content, from_email, [correo_destino])
            msg.attach_alternative(html_content, "text/html")
            msg.send()
        else:
            # Plantilla normal con adjunto
            html_content = render_to_string('laboratorio/correo_paciente.html', context)
            text_content = strip_tags(html_content)
            
            msg = EmailMultiAlternatives(subject, text_content, from_email, [correo_destino])
            msg.attach_alternative(html_content, "text/html")
            msg.attach(f"Resultados_Orden_{orden.id:05d}.pdf", pdf_content, 'application/pdf')
            msg.send()
            
    except Exception as e:
        print(f"Error crítico en envío de correo: {str(e)}")

@login_required
@rol_requerido(['laboratorio'])
@require_POST
def cancelar_orden_lab(request, orden_id):
    """ Función para descartar una orden desde el dashboard """
    orden = get_object_or_404(SolicitudExamen, id=orden_id)
    
    if orden.estado in ['Pendiente', 'Procesando']:
        orden.estado = 'Cancelada'
        # Le ponemos la fecha actual para que aparezca ordenada en el Historial
        orden.fecha_resultado = timezone.now() 
        orden.save()
        messages.warning(request, f'La Orden #{orden.id} ha sido descartada y enviada al historial.')
        
    return redirect('dashboard_lab')

@login_required
@rol_requerido(['laboratorio'])
def detalle_orden(request, orden_id):
    orden = get_object_or_404(SolicitudExamen, id=orden_id)
    
    # 1. PARSEO INTELIGENTE: Leer lo que pidió el médico y buscarlo en el catálogo
    nombres_solicitados = [e.strip() for e in orden.examenes_solicitados.split(',') if e.strip()]
    examenes_catalogo = []
    
    for nombre in nombres_solicitados:
        # Buscamos coincidencias (ignorando mayúsculas/minúsculas)
        examen = ExamenCatalogo.objects.filter(nombre__icontains=nombre, activo=True).first()
        if examen and examen not in examenes_catalogo:
            examenes_catalogo.append(examen)

    # 2. PROCESAMIENTO DEL FORMULARIO
    if request.method == 'POST':
        # ESCENARIO A: Carga de Resultados Estructurados (Formulario Dinámico)
        if 'guardar_resultados' in request.POST:
            for examen in examenes_catalogo:
                for parametro in examen.parametros.all():
                    valor_ingresado = request.POST.get(f'param_{parametro.id}', '').strip()
                    
                    if valor_ingresado:
                        es_anormal = False
                        try:
                            v_float = float(valor_ingresado.replace(',', '.'))
                            if parametro.rango_minimo and v_float < parametro.rango_minimo:
                                es_anormal = True
                            if parametro.rango_maximo and v_float > parametro.rango_maximo:
                                es_anormal = True
                        except ValueError:
                            if parametro.valor_referencia_texto and valor_ingresado.lower() != parametro.valor_referencia_texto.lower():
                                es_anormal = True

                        ResultadoDetalle.objects.update_or_create(
                            orden=orden,
                            parametro=parametro,
                            defaults={'valor_obtenido': valor_ingresado, 'es_anormal': es_anormal}
                        )

            # --- FASE 3: DESCUENTO AUTOMÁTICO DE REACTIVOS (INVENTARIO FARMACIA) ---
            for examen in examenes_catalogo:
                if examen.reactivo_necesario and examen.cantidad_reactivo > 0:
                    reactivo = examen.reactivo_necesario
                    cantidad_a_descontar = examen.cantidad_reactivo
                    
                    if reactivo.stock >= cantidad_a_descontar:
                        reactivo.stock -= cantidad_a_descontar
                        reactivo.save()
                        
                        MovimientoInventario.objects.create(
                            medicamento=reactivo,
                            tipo_movimiento='Salida',
                            cantidad=cantidad_a_descontar,
                            motivo=f'Consumo en Laboratorio (Orden #{orden.id} - {examen.nombre})',
                            usuario=request.user
                        )
                    else:
                        messages.warning(request, f"⚠️ Stock bajo de '{reactivo.nombre}'. El resultado médico se guardó, pero no se pudo descontar el reactivo del inventario de Farmacia.")
            # -------------------------------------------------------------------------
            
            # --- NUEVA MAGIA: GENERACIÓN AUTOMÁTICA DEL PDF E HILO DE CORREO ---
            resultados_guardados = orden.resultados_estructurados.all()
            if resultados_guardados.exists():
                template = get_template('laboratorio/pdf_resultados.html')
                context_pdf = {
                    'orden': orden,
                    'resultados': resultados_guardados,
                    'fecha_impresion': timezone.now(),
                }
                html = template.render(context_pdf)
                result_file = BytesIO()
                
                # Convertimos HTML a PDF
                pdf = pisa.pisaDocument(BytesIO(html.encode("UTF-8")), result_file)
                
                if not pdf.err:
                    # Lo guardamos físicamente en el campo de la base de datos
                    nombre_archivo = f"Resultados_{orden.id}.pdf"
                    pdf_bytes = result_file.getvalue()
                    orden.resultados_archivo.save(nombre_archivo, ContentFile(pdf_bytes))
                    
                    # Lanzamos el hilo de correo en segundo plano
                    hilo_correo = threading.Thread(
                        target=enviar_correo_resultados_async, 
                        args=(orden.id, pdf_bytes)
                    )
                    hilo_correo.start()
            # -------------------------------------------------------------------
            
            orden.estado = 'Realizado'
            orden.fecha_resultado = timezone.now()
            orden.save()
            messages.success(request, 'Resultados guardados y PDF generado exitosamente. Se ha enviado al paciente.')
            return redirect('dashboard_lab')

        # ESCENARIO B: Subida manual de PDF
        elif 'subir_pdf' in request.POST:
            pdf = request.FILES.get('archivo_resultados')
            if pdf:
                orden.resultados_archivo = pdf
                orden.estado = 'Realizado'
                orden.fecha_resultado = timezone.now()
                orden.save()
                
                # --- SEGURIDAD: Resetear puntero para evitar archivos de 0 bytes ---
                pdf.seek(0) 
                pdf_bytes = pdf.read()
                # -------------------------------------------------------------------
                
                threading.Thread(target=enviar_correo_resultados_async, args=(orden.id, pdf_bytes)).start()
                
                messages.success(request, 'PDF cargado y notificado al paciente.')
                return redirect('dashboard_lab')

    # Diccionario para rellenar los inputs si el usuario vuelve a abrir la orden
    resultados_existentes = {r.parametro.id: r.valor_obtenido for r in orden.resultados_estructurados.all()}

    context = {
        'orden': orden,
        'examenes_catalogo': examenes_catalogo,
        'tiene_catalogo': len(examenes_catalogo) > 0,
        'resultados_existentes': resultados_existentes,
    }
    return render(request, 'laboratorio/detalle_orden.html', context)

@login_required
@rol_requerido(['laboratorio'])
def api_estadisticas_laboratorio(request):
    periodo = request.GET.get('periodo', 'mes')
    hoy = timezone.localtime(timezone.now())

    # 1. Definir la fecha de inicio según el filtro
    if periodo == 'semana':
        fecha_inicio = hoy - timedelta(days=7)
    elif periodo == 'ano':
        fecha_inicio = hoy - timedelta(days=365)
    else:
        fecha_inicio = hoy - timedelta(days=30)

    # Filtrar las órdenes creadas en ese período
    ordenes = SolicitudExamen.objects.filter(fecha_solicitud__gte=fecha_inicio)

    # --- GRÁFICO 1: TOP EXÁMENES MÁS SOLICITADOS ---
    todos_examenes = []
    for orden in ordenes:
        if orden.examenes_solicitados:
            # Separamos el texto por comas y limpiamos espacios vacíos
            examenes = [e.strip() for e in orden.examenes_solicitados.split(',') if e.strip()]
            todos_examenes.extend(examenes)

    # Contamos cuántas veces se repite cada examen y sacamos el Top 10
    contador = Counter(todos_examenes)
    top_examenes = contador.most_common(10)

    # --- GRÁFICO 2: TENDENCIA DE ÓRDENES (FLUJO DIARIO) ---
    tendencia = ordenes.annotate(fecha=TruncDate('fecha_solicitud')) \
                       .values('fecha') \
                       .annotate(total=Count('id')) \
                       .order_by('fecha')

    fechas_tendencia = [t['fecha'].strftime('%d/%m') for t in tendencia]
    totales_tendencia = [t['total'] for t in tendencia]

    return JsonResponse({
        'top_examenes': {
            'labels': [e[0] for e in top_examenes],
            'data': [e[1] for e in top_examenes]
        },
        'tendencia': {
            'labels': fechas_tendencia,
            'data': totales_tendencia
        }
    })

@login_required
@rol_requerido(['laboratorio'])
def exportar_estadisticas_lab_excel(request):
    periodo = request.GET.get('periodo', 'mes')
    hoy = timezone.localtime(timezone.now())

    # 1. Filtramos por fecha según el periodo seleccionado
    if periodo == 'semana':
        fecha_inicio = hoy - timedelta(days=7)
        nombre_periodo = "Últimos 7 días"
    elif periodo == 'ano':
        fecha_inicio = hoy - timedelta(days=365)
        nombre_periodo = "Últimos 12 meses"
    else:
        fecha_inicio = hoy - timedelta(days=30)
        nombre_periodo = "Últimos 30 días"

    ordenes = SolicitudExamen.objects.filter(fecha_solicitud__gte=fecha_inicio)

    # 2. Cálculos (Top 10 y Tendencia)
    todos_examenes = []
    for orden in ordenes:
        if orden.examenes_solicitados:
            examenes = [e.strip() for e in orden.examenes_solicitados.split(',') if e.strip()]
            todos_examenes.extend(examenes)
            
    contador = Counter(todos_examenes)
    top_examenes = contador.most_common(10)

    tendencia = ordenes.annotate(fecha=TruncDate('fecha_solicitud')) \
                       .values('fecha') \
                       .annotate(total=Count('id')) \
                       .order_by('fecha')

    # 3. Preparar el Excel en Memoria
    output = io.BytesIO()
    workbook = xlsxwriter.Workbook(output, {'in_memory': True})
    
    formato_titulo = workbook.add_format({'bold': True, 'font_size': 14, 'font_color': 'white', 'bg_color': '#9333EA', 'align': 'center', 'valign': 'vcenter'}) # Morado
    formato_cabecera = workbook.add_format({'bold': True, 'bg_color': '#F3E8FF', 'border': 1})
    formato_celda = workbook.add_format({'border': 1})

    # ================= HOJA 1: TOP EXÁMENES =================
    ws_top = workbook.add_worksheet('Top Exámenes')
    ws_top.set_column('A:A', 35)
    ws_top.set_column('B:B', 15)
    ws_top.merge_range('A1:B2', f'Top 10 Exámenes ({nombre_periodo})', formato_titulo)
    ws_top.write('A4', 'Examen Solicitado', formato_cabecera)
    ws_top.write('B4', 'N° de Casos', formato_cabecera)
    
    fila = 4
    for ex in top_examenes:
        ws_top.write(fila, 0, ex[0], formato_celda)
        ws_top.write(fila, 1, ex[1], formato_celda)
        fila += 1
        
    if len(top_examenes) > 0:
        chart_top = workbook.add_chart({'type': 'bar'})
        chart_top.add_series({
            'name': 'Solicitudes',
            'categories': ['Top Exámenes', 4, 0, fila - 1, 0],
            'values':     ['Top Exámenes', 4, 1, fila - 1, 1],
            'fill':       {'color': '#9333EA'}
        })
        chart_top.set_title({'name': 'Top 10 Exámenes Más Solicitados'})
        chart_top.set_legend({'none': True})
        ws_top.insert_chart('D4', chart_top, {'x_scale': 1.5, 'y_scale': 1.2})

    # ================= HOJA 2: FLUJO DIARIO =================
    ws_flujo = workbook.add_worksheet('Flujo Diario')
    ws_flujo.set_column('A:A', 20)
    ws_flujo.set_column('B:B', 15)
    ws_flujo.merge_range('A1:B2', f'Flujo de Órdenes ({nombre_periodo})', formato_titulo)
    ws_flujo.write('A4', 'Fecha', formato_cabecera)
    ws_flujo.write('B4', 'Órdenes Creadas', formato_cabecera)
    
    fila_f = 4
    for t in tendencia:
        ws_flujo.write(fila_f, 0, t['fecha'].strftime('%d/%m/%Y'), formato_celda)
        ws_flujo.write(fila_f, 1, t['total'], formato_celda)
        fila_f += 1
        
    if tendencia.exists():
        chart_flujo = workbook.add_chart({'type': 'line'})
        chart_flujo.add_series({
            'name': 'Órdenes',
            'categories': ['Flujo Diario', 4, 0, fila_f - 1, 0],
            'values':     ['Flujo Diario', 4, 1, fila_f - 1, 1],
            'line':       {'color': '#9333EA', 'width': 2.5}
        })
        chart_flujo.set_title({'name': 'Volumen de Órdenes por Día'})
        chart_flujo.set_legend({'none': True})
        ws_flujo.insert_chart('D4', chart_flujo, {'x_scale': 1.5, 'y_scale': 1.2})

    workbook.close()
    output.seek(0)
    
    # 4. Devolver la respuesta como archivo descargable
    response = HttpResponse(
        output.read(), 
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = f'attachment; filename="Estadisticas_Laboratorio_{hoy.strftime("%Y%m%d")}.xlsx"'
    
    return response

login_required
@rol_requerido(['laboratorio'])
def editar_perfil_lab(request):
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
            return redirect('editar_perfil_lab')

        except Exception as e:
            messages.error(request, f"Error al guardar los datos: {str(e)}")

    context = {
        'usuario': usuario,
    }
    return render(request, 'laboratorio/editar_perfil.html', context)

