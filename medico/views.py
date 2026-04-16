from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from datetime import timedelta
from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.template.loader import get_template
from django.urls import reverse
from django.core.files.base import ContentFile
from django.views.decorators.http import require_POST
from django.db.models import Q, Count
from django.core.exceptions import PermissionDenied
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from xhtml2pdf import pisa
from .models import ExpedienteBase, ConsultaEvolucion, Recipe, ConstanciaMedica
from administracion.models import Cita, Paciente, Medico
from farmacia.models import OrdenFarmacia
from laboratorio.models import SolicitudExamen
from usuarios.decorators import rol_requerido
import json
import base64
import io
import xlsxwriter

#FUNCIÓN AUXILIAR DE LIMPIEZA (altura)--NO BORRAR
def limpiar_numero(valor):
    """
    Intenta convertir un valor a float/decimal.
    Si falla (por comas o texto), devuelve None.
    """
    if not valor:
        return None
    try:
        # Reemplazamos coma por punto por si el usuario usó formato europeo
        valor = valor.replace(',', '.')
        return float(valor)
    except ValueError:
        return None

#1.DASHBOARD
@login_required
@rol_requerido(['medico'])
def dashboard_medico(request):
    # 1. Obtener la fecha de hoy
    hoy = timezone.localtime(timezone.now()).date()

    # 2. IDENTIFICAR AL MÉDICO QUE INICIÓ SESIÓN
    try:
        medico_perfil = request.user.medico 
    except AttributeError:
        return redirect('landing_page')

    # 3. FILTRAR CITAS 
    pacientes_espera = Cita.objects.filter(medico=medico_perfil, fecha=hoy).exclude(estado='Atendido').select_related('paciente').order_by('hora')

    paginator = Paginator(pacientes_espera, 7) 
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    # 4. OBTENER TODOS LOS PACIENTES DEL MÉDICO PARA EL BUSCADOR DE CONTROLES
    pacientes_ids = ConsultaEvolucion.objects.filter(medico=medico_perfil).values_list('expediente__paciente_id', flat=True).distinct()
    mis_pacientes = Paciente.objects.filter(id__in=pacientes_ids).order_by('nombres')

    # 5. Contexto para la plantilla
    context = {
        'pacientes_espera': pacientes_espera, 
        'page_obj': page_obj,                 
        'fecha': hoy,
        'medico': medico_perfil,
        'mis_pacientes': mis_pacientes 
    }
    return render(request, 'medico/dashboard.html', context)

#1.5 NUEVA FUNCION - NUEVA LOGICA DE HISTORIA CLINICA
@login_required
@rol_requerido(['medico'])
def crear_control_rapido(request):
    if request.method == 'POST':
        paciente_id = request.POST.get('paciente_id')
        
        if not paciente_id:
            messages.error(request, "Debe seleccionar un paciente para iniciar el control.")
            return redirect('dashboard_medico')
            
        # Redirigimos al formulario de historia manual, pero pasándole un parámetro especial 
        # para indicarle que es un control de un paciente ya registrado.
        # (Modificaremos crear_historia_manual más adelante para que lea este parámetro y se adapte)
        url = reverse('crear_historia_manual')
        return redirect(f"{url}?control_paciente_id={paciente_id}")
        
    return redirect('dashboard_medico')

#2.GENERAR PDF
@login_required
@rol_requerido(['medico'])
def generar_pdf_historia(request, historia_id):
    historia = get_object_or_404(ConsultaEvolucion, id=historia_id)

    if hasattr(request.user, 'medico') and historia.medico != request.user.medico:
        raise PermissionDenied("Acceso denegado. Solo el médico tratante puede descargar esta historia clínica.")

    template_path = 'medico/pdf_historia.html'
    context = {'h': historia}
    
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="historia_{historia.expediente.paciente.cedula}.pdf"'
    
    template = get_template(template_path)
    html = template.render(context)
    
    pisa_status = pisa.CreatePDF(html, dest=response)
    if pisa_status.err:
        return HttpResponse('Error al generar PDF', status=500)
    return response

#3 ATENDER PACIENTE 
@login_required
@rol_requerido(['medico'])
def atender_paciente(request, cita_id):
    cita = get_object_or_404(Cita, id=cita_id)

    if cita.medico != request.user.medico:
        raise PermissionDenied("No tiene permisos para atender una cita asignada a otro médico.")

    paciente = cita.paciente

    if request.method == 'POST':
        try:
            peso_safe = limpiar_numero(request.POST.get('peso'))
            talla_safe = limpiar_numero(request.POST.get('talla'))
            temp_safe = limpiar_numero(request.POST.get('temp'))

            # Validación manual: Si el usuario escribió algo pero la limpieza devolvió None
            if request.POST.get('talla') and talla_safe is None:
                messages.warning(request, "La estatura tenía un formato incorrecto y no se guardó. Use puntos (ej. 1.70).")
            
            if request.POST.get('peso') and peso_safe is None:
                messages.warning(request, "El peso tenía un formato incorrecto y no se guardó.")

            # --- NUEVA LÓGICA: GESTIÓN DEL EXPEDIENTE BASE ---
            expediente, creado = ExpedienteBase.objects.get_or_create(paciente=paciente)
            
            # Actualizamos los antecedentes en el expediente base
            if request.POST.get('ant_personales'):
                expediente.antecedentes_personales = request.POST.get('ant_personales')
            if request.POST.get('ant_familiares'):
                expediente.antecedentes_familiares = request.POST.get('ant_familiares')
            expediente.save()

            # --- PROCESAMIENTO DEL DIAGNÓSTICO (CIE-10 o Personalizado) ---
            diagnostico_principal = request.POST.get('diagnostico')
            if diagnostico_principal == 'Otro - Especificar manualmente':
                diagnostico_final = request.POST.get('diagnostico_otro')
            else:
                diagnostico_final = diagnostico_principal

            # --- NUEVA LÓGICA: CREAR LA CONSULTA/EVOLUCIÓN ---
            historia = ConsultaEvolucion.objects.create(
                expediente=expediente, 
                medico=cita.medico,
                cita=cita,
                
                # Signos Vitales 
                tension_arterial=request.POST.get('ta'),
                frecuencia_cardiaca=request.POST.get('fc'),
                frecuencia_respiratoria=request.POST.get('fr'),
                saturacion_oxigeno=request.POST.get('sato2'),
                temperatura=request.POST.get('temp'), 
                glicemia=request.POST.get('glic'),
                peso=peso_safe,  
                talla=talla_safe, 
                
                # Datos de la visita
                motivo_consulta=request.POST.get('motivo'),
                enfermedad_actual=request.POST.get('enfermedad_actual'),
                examen_fisico=request.POST.get('examen_fisico'),
                diagnostico=diagnostico_final, # ¡AQUÍ SE INYECTA EL FILTRO!
                plan_tratamiento=request.POST.get('plan'),
                
                # Anexos
                tipo_anexo=request.POST.get('tipo_anexo'),
                archivo_anexo=request.FILES.get('archivo_anexo'),
            )
            
            cita.estado = 'Atendido'
            cita.save()

            # Lógica de Redirección (¡ERROR DE TIPEO 'ccion' CORREGIDO!)
            accion = request.POST.get('accion')
            
            if accion == 'guardar_pdf':
                messages.success(request, f"Historia guardada correctamente. Generando PDF...")
                return redirect(f'/medico/?pdf_id={historia.id}')
            
            # --- NUEVA ACCIÓN: GUARDAR Y EMITIR CONSTANCIA ---
            elif accion == 'guardar_constancia':
                from django.urls import reverse
                messages.success(request, "Historia guardada con éxito. Ya puede redactar la constancia.")
                
                # Protegemos la redirección por si el nombre de la URL cambia
                try:
                    url_expediente = reverse('ver_expediente', kwargs={'paciente_id': paciente.id})
                except:
                    url_expediente = reverse('ver_expediente_unificado', kwargs={'paciente_id': paciente.id})
                    
                return redirect(f"{url_expediente}?constancia=true")
            # -------------------------------------------------
            
            else:
                messages.success(request, f"Historia guardada correctamente.")
                return redirect('dashboard_medico')

        # ¡FALTABA ESTE BLOQUE PARA CERRAR EL TRY!
        except Exception as e:
            messages.error(request, f"Ocurrió un error inesperado: {str(e)}")
            return redirect('dashboard_medico')

    context = {
        'cita': cita,
        'paciente': paciente,
        'es_cita_agendada': True
    }
    return render(request, 'medico/formulario_historia.html', context)

#4 PX emergencia / manual / control
@login_required
@rol_requerido(['medico'])
def crear_historia_manual(request):
    try:
        medico_perfil = request.user.medico
    except AttributeError:
        return redirect('landing_page')

    hoy = timezone.localtime(timezone.now()).date()

    # --- NUEVO: DETECTAR SI ESTAMOS EN MODO "CONTROL" ---
    control_paciente_id = request.GET.get('control_paciente_id')
    paciente_control = None
    expediente_control = None
    modo_control = False

    if control_paciente_id:
        paciente_control = get_object_or_404(Paciente, id=control_paciente_id)
        expediente_control = ExpedienteBase.objects.filter(paciente=paciente_control).first()
        modo_control = True

    cita_id_url = request.GET.get('cita_id')
    cita_preseleccionada_id = None
    if cita_id_url:
        try:
            cita_preseleccionada_id = int(cita_id_url)
        except ValueError:
            pass

    if request.method == 'POST':
        try:
            tipo_registro = request.POST.get('tipo_registro')
            paciente_obj = None

            # --- ESCENARIO A: PACIENTE NUEVO (EMERGENCIA) ---
            if tipo_registro == 'nuevo':
                cedula_nueva = request.POST.get('nuevo_cedula')
                nacionalidad = request.POST.get('nuevo_nacionalidad')
                
                if Paciente.objects.filter(cedula=cedula_nueva, nacionalidad=nacionalidad).exists():
                    messages.error(request, "Error: Ya existe un paciente con esa cédula.")
                    return redirect('crear_historia_manual')

                paciente_obj = Paciente.objects.create(
                    nombres=request.POST.get('nuevo_nombre'),
                    nacionalidad=nacionalidad,
                    cedula=cedula_nueva,
                    tipo_sangre=request.POST.get('nuevo_sangre'),
                    fecha_nacimiento=request.POST.get('nuevo_fecha_nacimiento') or None,
                    telefono = None,
                    tiene_seguro=False 
                )
                messages.success(request, f"Paciente de emergencia registrado: {paciente_obj.nombres}")

            # --- ESCENARIO B: CONSULTA DE CONTROL ---
            elif tipo_registro == 'control':
                paciente_id = request.POST.get('paciente_control_id')
                paciente_obj = get_object_or_404(Paciente, id=paciente_id)

            # --- ESCENARIO C: PACIENTE AGENDADO ---
            else:
                paciente_id = request.POST.get('paciente')
                if not paciente_id:
                    messages.error(request, "Debe seleccionar un paciente de la lista.")
                    return redirect('crear_historia_manual')
                paciente_obj = get_object_or_404(Paciente, id=paciente_id)

            # --- GESTIÓN DEL EXPEDIENTE BASE ---
            expediente, creado = ExpedienteBase.objects.get_or_create(paciente=paciente_obj)
            
            if request.POST.get('nuevo_sangre'):
                expediente.tipo_sangre = request.POST.get('nuevo_sangre')
            
            # NUEVO: Guardar alergias
            if request.POST.get('alergias') is not None:
                expediente.alergias = request.POST.get('alergias')

            if request.POST.get('ant_personales'):
                expediente.antecedentes_personales = request.POST.get('ant_personales')
            if request.POST.get('ant_familiares'):
                expediente.antecedentes_familiares = request.POST.get('ant_familiares')
            expediente.save()

            # --- PROCESAMIENTO DEL DIAGNÓSTICO (CIE-10 o Personalizado) ---
            diagnostico_principal = request.POST.get('diagnostico')
            if diagnostico_principal == 'Otro - Especificar manualmente':
                diagnostico_final = request.POST.get('diagnostico_otro')
            else:
                diagnostico_final = diagnostico_principal

            # --- GUARDAR HISTORIA / EVOLUCIÓN ---
            peso_safe = limpiar_numero(request.POST.get('peso'))
            talla_safe = limpiar_numero(request.POST.get('talla'))

            historia = ConsultaEvolucion.objects.create(
                expediente=expediente,
                medico=medico_perfil,
                cita=None, 
                
                tension_arterial=request.POST.get('ta'),
                frecuencia_cardiaca=request.POST.get('fc'),
                frecuencia_respiratoria=request.POST.get('fr'),
                saturacion_oxigeno=request.POST.get('sato2'),
                temperatura=request.POST.get('temp'),
                glicemia=request.POST.get('glic'),
                peso=peso_safe,
                talla=talla_safe,
                
                motivo_consulta=request.POST.get('motivo'),
                enfermedad_actual=request.POST.get('enfermedad_actual'),
                examen_fisico=request.POST.get('examen_fisico'),
                diagnostico=diagnostico_final, # ¡AQUÍ SE INYECTA EL FILTRO!
                plan_tratamiento=request.POST.get('plan'),
                
                tipo_anexo=request.POST.get('tipo_anexo'),
                archivo_anexo=request.FILES.get('archivo_anexo')
            )

            # CERRAR CITA SI APLICA
            if tipo_registro == 'existente':
                cita_pendiente = Cita.objects.filter(
                    medico=medico_perfil, paciente=paciente_obj, fecha=hoy, estado='Pendiente'
                ).first()
                if cita_pendiente:
                    cita_pendiente.estado = 'Atendido'
                    cita_pendiente.save()

            # --- GESTIÓN DE ACCIONES Y REDIRECCIÓN ---
            accion = request.POST.get('accion')
            
            if accion == 'guardar_pdf':
                return redirect(f'/medico/?pdf_id={historia.id}')
                
            # --- NUEVA ACCIÓN: GUARDAR Y EMITIR CONSTANCIA ---
            elif accion == 'guardar_constancia':
                from django.urls import reverse # Importamos aquí de forma segura
                messages.success(request, "Historia guardada con éxito. Ya puede redactar la constancia.")
                
                # NOTA: Si en tu urls.py la ruta se llama 'ver_expediente_unificado', cambia el texto abajo.
                # Normalmente se llama 'ver_expediente'
                try:
                    url_expediente = reverse('ver_expediente', kwargs={'paciente_id': paciente_obj.id})
                except:
                    url_expediente = reverse('ver_expediente_unificado', kwargs={'paciente_id': paciente_obj.id})
                    
                return redirect(f"{url_expediente}?constancia=true")
            # -------------------------------------------------
            
            else:
                messages.success(request, "Historia / Control guardado correctamente.")
                return redirect('dashboard_medico')

        except Exception as e:
            messages.error(request, f"Error inesperado: {str(e)}")
            return redirect('crear_historia_manual')

    else:
        citas_pendientes = Cita.objects.filter(
            medico=medico_perfil, fecha=hoy, estado='Pendiente'
        ).select_related('paciente')

        context = {
            'citas_pendientes': citas_pendientes,
            'cita_preseleccionada_id': cita_preseleccionada_id,
            'modo_control': modo_control, 
            'paciente_control': paciente_control,
            'expediente_control': expediente_control
        }
        return render(request, 'medico/formulario_historia.html', context)

#5.Historial px
@login_required
@rol_requerido(['medico'])
def historial_medico(request):
    try:
        medico_perfil = request.user.medico
    except AttributeError:
        return redirect('landing_page')

    #1. Capturamos lo que el usuario escribe en el buscador
    query = request.GET.get('q', '')

    #2. Traemos las historias del médico y las ordenamos por lo más nuevo primero
    historias = ConsultaEvolucion.objects.filter(medico=medico_perfil).select_related('expediente__paciente').order_by('-fecha', '-hora')

    #3. filtramos por nombre o cédula del paciente
    if query:
        historias = historias.filter(
            Q(expediente__paciente__nombres__icontains=query) |
            Q(expediente__paciente__cedula__icontains=query)
        )

    paginator = Paginator(historias, 5)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    pacientes_ids = ConsultaEvolucion.objects.filter(medico=medico_perfil).values_list('expediente__paciente_id', flat=True).distinct()
    mis_pacientes = Paciente.objects.filter(id__in=pacientes_ids).order_by('nombres')
    # 5. Enviamos la página actual y la variable de búsqueda al template
    context = {
        'page_obj': page_obj,
        'query': query,
        'mis_pacientes': mis_pacientes
    }
    
    return render(request, 'medico/historial_listado.html', context)

#6.Recipes
@login_required
@rol_requerido(['medico'])
def crear_recipe(request):
    try:
        medico_perfil = request.user.medico
    except AttributeError:
        return redirect('landing_page')

    # NUEVO: Detectar si esta vista se abrió como ventana emergente (popup)
    is_popup = request.GET.get('popup', request.POST.get('popup', '0'))

    if request.method == 'POST':
        # Capturamos datos del paciente registrado o manual
        paciente_id = request.POST.get('paciente_id')
        nombre_manual = request.POST.get('nombre_manual')
        cedula_manual = request.POST.get('cedula_manual')
        fecha_emision = request.POST.get('fecha_emision')
        
        medicamentos = request.POST.get('medicamentos')
        indicaciones = request.POST.get('indicaciones')
        accion = request.POST.get('accion')

        paciente_seleccionado = None
        if paciente_id:
            paciente_seleccionado = get_object_or_404(Paciente, id=paciente_id)
            nombre_final = f"{paciente_seleccionado.nombres}"
            cedula_final = paciente_seleccionado.cedula
        else:
            nombre_final = nombre_manual
            cedula_final = cedula_manual
            
            if cedula_final:
                cedula_final = cedula_final.strip()
                if not cedula_final.isdigit() or len(cedula_final) < 6 or len(cedula_final) > 9:
                    messages.error(request, "Error de validación: La cédula debe contener entre 6 y 9 dígitos numéricos (sin puntos ni letras).")
                    
                    # Si hubo error y era popup, mantenemos el parámetro popup=1 al recargar
                    if is_popup == '1':
                        return redirect(f"{reverse('crear_recipe')}?popup=1")
                    return redirect('crear_recipe')

        recipe_nuevo = Recipe.objects.create(
            medico=medico_perfil,
            nombre_paciente=nombre_final,
            cedula_paciente=cedula_final,
            medicamentos=medicamentos,
            indicaciones=indicaciones
        )

        if accion == 'exportar_pdf':
            messages.success(request, "Récipe guardado exitosamente. Generando PDF...")
            
            if is_popup == '1':
                return redirect('pdf_recipe', recipe_id=recipe_nuevo.id)
            else:
                url_dashboard = reverse('dashboard_medico')
                return redirect(f"{url_dashboard}?recipe_pdf_id={recipe_nuevo.id}")
            
        elif accion == 'enviar_farmacia':
            if nombre_final: # Verificamos que al menos haya un nombre (registrado o manual)
                OrdenFarmacia.objects.create(
                    paciente=paciente_seleccionado, # Si es manual, esto quedará en None
                    nombre_paciente=nombre_manual,
                    cedula_paciente=cedula_manual,
                    medico=medico_perfil,
                    receta_medica_texto=f"Medicamentos solicitados:\n{medicamentos}\n\nIndicaciones:\n{indicaciones}",
                    estado='Pendiente'
                )
                messages.success(request, f"El récipe de {nombre_final} se ha enviado a la farmacia.")
            else:
                messages.warning(request, "Error: Debe seleccionar un paciente o ingresar sus datos manualmente.")
            
            # NUEVO: Si es popup, inyectamos un script que simplemente cierra la ventana flotante 
            # y deja al médico en su historia clínica original sin borrar lo que ha escrito.
            if is_popup == '1':
                return HttpResponse("<script>window.close();</script>")
            
            return redirect('dashboard_medico')

    # CORRECCIÓN: Salto a través de ExpedienteBase usando doble guion bajo
    pacientes_ids = ConsultaEvolucion.objects.filter(medico=medico_perfil).values_list('expediente__paciente_id', flat=True).distinct()
    pacientes_del_medico = Paciente.objects.filter(id__in=pacientes_ids).order_by('nombres')
    
    context = {
        'pacientes': pacientes_del_medico,
        'is_popup': is_popup,
        'pre_paciente_id': request.GET.get('paciente_id', ''),
        'pre_nombre': request.GET.get('nombre', ''),
        'pre_cedula': request.GET.get('cedula', ''),
    }
    
    return render(request, 'medico/crear_recipe.html', context)

@login_required
@rol_requerido(['medico'])
def generar_pdf_recipe(request, recipe_id):
    recipe = get_object_or_404(Recipe, id=recipe_id)
    template_path = 'medico/recipe_pdf.html'
    
    context = { 'recipe': recipe }
    
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="recipe_{recipe.id}.pdf'

    template = get_template(template_path)
    html = template.render(context)

    pisa_status = pisa.CreatePDF(html, dest=response)

    if pisa_status.err:
        return HttpResponse(f'Tuvimos errores al generar el PDF del récipe: <pre>{html}</pre>')
    
    return response

@login_required
@rol_requerido(['medico'])
def solicitar_examenes(request):
    medico_perfil = request.user.medico
    examenes_lab = [
        'Hematología', 'Glicemia', 'Urea', 'Creatinina', 'Ácido Úrico', 
        'Colesterol', 'Triglicéridos', 'Perfil Lipídico', 'PT', 'PTT', 
        'Fibrinógeno', 'HIV', 'VDRL', 'VSG', 'HCG cualitativa', 'PCR', 
        'Proteína T y F', 'Calcio', 'Fósforo', 'Mágnesio', 'TGO - TGP', 
        'Bilirrubina', 'Fosfatasa alcalina', 'Drogas de abuso', 'Heces', 'Orina'
    ]
    examenes_img = ['Rayos X', 'Ecosonograma']

    if request.method == 'POST':
        try:
            # 1. Capturamos si es paciente registrado o manual
            tipo_paciente = request.POST.get('tipo_paciente')
            
            # 2. Asignamos nombre y cédula dependiendo de la opción
            paciente_seleccionado = None
            if tipo_paciente == 'registrado':
                paciente_id = request.POST.get('paciente_id')
                paciente_seleccionado = get_object_or_404(Paciente, id=paciente_id)
                nombre = paciente_seleccionado.nombres
                cedula = paciente_seleccionado.cedula
            else:
                nombre = request.POST.get('nombre_manual')
                cedula = request.POST.get('cedula_manual')
            
            # 3. Datos de los exámenes
            seleccionados = request.POST.getlist('examenes')
            examenes_str = ", ".join(seleccionados)
            otros = request.POST.get('otros_detalle')
            observacion = request.POST.get('observacion')
            accion = request.POST.get('accion')
            
            # NUEVO: Capturamos el correo del paciente
            correo_paciente = request.POST.get('correo_paciente')
            
            # 4. Validar que al menos haya seleccionado un examen o escrito algo en otros
            if not examenes_str and not otros:
                messages.warning(request, "Debe seleccionar al menos un examen o escribirlo en la casilla de 'Otros'.")
                return redirect('solicitar_examenes')
            
            # 5. Creamos la orden
            orden = SolicitudExamen.objects.create(
                paciente=paciente_seleccionado, # MEJORA: Guardamos el enlace al paciente para que la búsqueda de correo automático funcione
                nombre_paciente=nombre,
                cedula_paciente=cedula,
                correo_paciente=correo_paciente, # NUEVO: Guardamos el correo en la BD
                medico=medico_perfil,
                examenes_solicitados=examenes_str,
                otros=otros,
                observacion=observacion
            )
            
            if accion == 'generar_pdf':
                messages.success(request, f"Orden registrada. Generando PDF de {nombre}...")
                return redirect(f"/medico/?pdf_orden_id={orden.id}")
            else:
                messages.success(request, f"Orden enviada al laboratorio a nombre de {nombre}")
                return redirect('dashboard_medico')
            
        except Exception as e:
            messages.error(request, f"Error al enviar la solicitud: {str(e)}")

    # CORRECCIÓN: Salto a través de ExpedienteBase usando doble guion bajo
    pacientes_ids = ConsultaEvolucion.objects.filter(medico=medico_perfil).values_list('expediente__paciente_id', flat=True).distinct()
    pacientes_del_medico = Paciente.objects.filter(id__in=pacientes_ids).order_by('nombres')

    return render(request, 'medico/solicitar_examenes.html', {
        'examenes_lab': examenes_lab, 
        'examenes_img': examenes_img,
        'pacientes': pacientes_del_medico # Pasamos la lista al template
    })

@login_required
@rol_requerido(['medico'])
def editar_perfil_medico(request):
    # 1. Cargamos el Usuario principal (para first_name, last_name, cedula, mpps, cm)
    usuario = request.user 
    
    # 2. Cargamos el perfil de Medico vinculado (para la foto_perfil)
    # Usamos getattr por seguridad, por si acaso el usuario no tiene perfil médico creado
    medico = getattr(usuario, 'medico', None)

    if request.method == 'POST':
        try:
            # --- PROCESAR TEXTOS (Van a la tabla Usuario) ---
            nombre_completo = request.POST.get('nombre_completo', '').strip()
            
            if nombre_completo:
                # Picamos el texto en el primer espacio
                partes = nombre_completo.split(' ', 1)
                usuario.first_name = partes[0]
                usuario.last_name = partes[1] if len(partes) > 1 else ''

            usuario.cedula = request.POST.get('cedula', usuario.cedula)
            usuario.mpps = request.POST.get('mpps', usuario.mpps)
            usuario.cm = request.POST.get('cm', usuario.cm)
            
            usuario.save()

            # --- PROCESAR IMAGEN (Va a la tabla Medico) ---
            if medico and 'foto_perfil' in request.FILES:
                medico.foto_perfil = request.FILES['foto_perfil']
                medico.save()

            messages.success(request, "Perfil actualizado correctamente.")
            return redirect('editar_perfil_medico')

        except Exception as e:
            messages.error(request, f"Error al guardar: {str(e)}")

    # Enviamos ambas variables al HTML
    context = {
        'usuario': usuario,
        'medico': medico
    }
    return render(request, 'medico/editar_perfil.html', context)

@login_required
@rol_requerido(['medico'])
def cargar_firma_sello(request):
    try:
        medico = request.user.medico
    except AttributeError:
        return redirect('landing_page')

    if request.method == 'POST':
        try:
            from rembg import remove
            # ==========================================
            # 1. PROCESAR FIRMA CON INTELIGENCIA ARTIFICIAL
            # ==========================================
            if 'firma' in request.FILES:
                archivo_original = request.FILES['firma']
                
                # Leemos la imagen original
                input_bytes = archivo_original.read()
                
                # La IA elimina el fondo
                output_bytes = remove(input_bytes)
                
                # Creamos un nombre de archivo forzando la extensión .png
                nombre_archivo = f"firma_dr_{medico.id}.png"
                
                # Borramos la firma vieja si existía para no acumular basura
                if medico.firma:
                    medico.firma.delete(save=False)
                    
                # Guardamos la nueva imagen procesada
                medico.firma.save(nombre_archivo, ContentFile(output_bytes), save=False)
                
            elif 'eliminar_firma' in request.POST:
                if medico.firma:
                    medico.firma.delete(save=False)
                    medico.firma = None

            # ==========================================
            # 2. PROCESAR SELLO CON INTELIGENCIA ARTIFICIAL
            # ==========================================
            if 'sello' in request.FILES:
                archivo_original = request.FILES['sello']
                
                input_bytes = archivo_original.read()
                output_bytes = remove(input_bytes)
                
                nombre_archivo = f"sello_dr_{medico.id}.png"
                
                if medico.sello:
                    medico.sello.delete(save=False)
                    
                medico.sello.save(nombre_archivo, ContentFile(output_bytes), save=False)
                
            elif 'eliminar_sello' in request.POST:
                if medico.sello:
                    medico.sello.delete(save=False)
                    medico.sello = None

            # Guardamos todos los cambios en la base de datos
            medico.save()
            messages.success(request, "¡Imágenes procesadas y guardadas con fondo transparente!")
            return redirect('cargar_firma_sello')

        except Exception as e:
            messages.error(request, f"Ocurrió un error al procesar la imagen: {str(e)}")

    context = {
        'medico': medico
    }
    return render(request, 'medico/cargar_firma_sello.html', context)

@login_required
@rol_requerido(['medico', 'laboratorio'])
def resultados_examenes(request):
    # Traemos solo las órdenes finalizadas ("Realizado") que pertenecen a este médico en particular
    resultados = SolicitudExamen.objects.filter(
        medico=request.user.medico, 
        estado='Realizado'
    ).order_by('-fecha_resultado')
    
    # Lógica de la barra de búsqueda
    query = request.GET.get('q')
    if query:
        # Filtramos buscando coincidencias en el nombre o en la cédula (ya sea texto libre o el paciente enlazado)
        resultados = resultados.filter(
            Q(nombre_paciente__icontains=query) |
            Q(cedula_paciente__icontains=query) |
            Q(paciente__nombres__icontains=query) |
            Q(paciente__cedula__icontains=query)
        )

    ids_registrados = SolicitudExamen.objects.filter(
        medico=request.user.medico, estado='Realizado', paciente__isnull=False
    ).values_list('paciente_id', flat=True).distinct()
    
    # Asumo que tienes el modelo Paciente importado arriba.
    pacientes_registrados = Paciente.objects.filter(id__in=ids_registrados)
    
    # 2. Pacientes Manuales (Extraemos nombre y cédula guardados como texto simple)
    pacientes_manuales = SolicitudExamen.objects.filter(
        medico=request.user.medico, estado='Realizado', paciente__isnull=True
    ).values('nombre_paciente', 'cedula_paciente').distinct()
    
    # 3. Unimos ambas listas en una sola estructura unificada para el HTML
    mis_pacientes = []
    for p in pacientes_registrados:
        mis_pacientes.append({'nombres': p.nombres, 'cedula': p.cedula})
        
    for p in pacientes_manuales:
        # Evitamos meter registros vacíos o corruptos
        if p['nombre_paciente'] and p['cedula_paciente']:
            mis_pacientes.append({'nombres': p['nombre_paciente'], 'cedula': p['cedula_paciente']})

    context = {
        'resultados': resultados,
        'query': query, 
        'mis_pacientes': mis_pacientes, # <-- Pasamos la lista al menú desplegable
    }
    return render(request, 'medico/resultados_examenes.html', context)

@login_required
@rol_requerido(['medico'])
def generar_pdf_orden(request, orden_id):
    orden = get_object_or_404(SolicitudExamen, id=orden_id)
    template_path = 'medico/orden_pdf.html'
    
    context = { 'orden': orden }
    
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="orden_examenes_{orden.cedula_paciente}.pdf"'

    template = get_template(template_path)
    html = template.render(context)

    pisa_status = pisa.CreatePDF(html, dest=response)

    if pisa_status.err:
        return HttpResponse('Tuvimos errores al generar el PDF', status=500)
    
    return response

@login_required
@rol_requerido(['medico'])
def eliminar_historia(request, historia_id):
    # 1. Buscamos la historia asegurándonos que pertenezca al médico actual
    historia = get_object_or_404(ConsultaEvolucion, id=historia_id, medico=request.user.medico)
    nombre_paciente = historia.paciente.nombres
    
    # 2. Eliminamos permanentemente el registro de la base de datos
    historia.delete()
    
    # 3. Avisamos al usuario y recargamos la página
    messages.success(request, f"El registro médico de {nombre_paciente} ha sido eliminado de su historial.")
    return redirect('historial_medico')

@login_required
@rol_requerido(['medico'])
def ver_expediente_unificado(request, paciente_id):
    paciente = get_object_or_404(Paciente, id=paciente_id)
    
    # Obtenemos el expediente (o lo creamos por seguridad si no existe)
    expediente, creado = ExpedienteBase.objects.get_or_create(paciente=paciente)
    
    # Traemos todas las consultas ordenadas de la más nueva a la más vieja
    consultas = expediente.consultas.all().order_by('-fecha', '-hora')
    
    # --- PREPARACIÓN DE DATOS PARA LOS GRÁFICOS ---
    # Para los gráficos, queremos el orden cronológico normal (de lo más viejo a lo más nuevo)
    consultas_grafico = list(reversed(consultas))
    fechas_grafico = [c.fecha.strftime("%d/%m/%Y") for c in consultas_grafico]
    
    sis_data = []
    dia_data = []
    peso_data = []

    for c in consultas_grafico:
        # Extraer Tensión Arterial (ej. "120/80")
        if c.tension_arterial and '/' in c.tension_arterial:
            try:
                partes = c.tension_arterial.split('/')
                sis_data.append(int(partes[0].strip()))
                dia_data.append(int(partes[1].strip()))
            except:
                sis_data.append(None)
                dia_data.append(None)
        else:
            sis_data.append(None)
            dia_data.append(None)
            
        # Extraer Peso
        peso_data.append(float(c.peso) if c.peso else None)

    context = {
        'paciente': paciente,
        'expediente': expediente,
        'consultas': consultas,
        
        # Convertimos las listas a JSON para que JavaScript (Chart.js) pueda leerlas
        'fechas_json': json.dumps(fechas_grafico),
        'sis_json': json.dumps(sis_data),
        'dia_json': json.dumps(dia_data),
        'peso_json': json.dumps(peso_data),
    }
    
    return render(request, 'medico/ver_expediente.html', context)

@login_required
@rol_requerido(['medico'])
def api_estadisticas_medico(request):
    medico = request.user.medico
    periodo = request.GET.get('periodo', 'mes')
    hoy = timezone.now().date()

    if periodo == 'semana':
        fecha_inicio = hoy - timedelta(days=7)
    elif periodo == 'ano':
        fecha_inicio = hoy - timedelta(days=365)
    else: 
        fecha_inicio = hoy - timedelta(days=30)

    # 1. MORBILIDAD DEL MÉDICO (Sus consultas)
    consultas_medico = ConsultaEvolucion.objects.filter(medico=medico, fecha__gte=fecha_inicio)
    diag_medico = consultas_medico.exclude(diagnostico__isnull=True).exclude(diagnostico__exact='').values('diagnostico').annotate(total=Count('id')).order_by('-total')[:5]
    
    labels_medico = [d['diagnostico'][:25] + "..." if len(d['diagnostico']) > 25 else d['diagnostico'] for d in diag_medico]
    data_medico = [d['total'] for d in diag_medico]

    # 2. TENDENCIA DEL MÉDICO
    tendencia = consultas_medico.values('fecha').annotate(total=Count('id')).order_by('fecha')
    labels_tendencia = [t['fecha'].strftime('%d/%m') for t in tendencia]
    data_tendencia = [t['total'] for t in tendencia]

    # 3. MORBILIDAD GLOBAL (Todas las consultas del ambulatorio)
    consultas_globales = ConsultaEvolucion.objects.filter(fecha__gte=fecha_inicio)
    diag_global = consultas_globales.exclude(diagnostico__isnull=True).exclude(diagnostico__exact='').values('diagnostico').annotate(total=Count('id')).order_by('-total')[:5]
    
    labels_global = [d['diagnostico'][:25] + "..." if len(d['diagnostico']) > 25 else d['diagnostico'] for d in diag_global]
    data_global = [d['total'] for d in diag_global]

    return JsonResponse({
        'medico': {'labels': labels_medico, 'data': data_medico},
        'tendencia': {'labels': labels_tendencia, 'data': data_tendencia},
        'global': {'labels': labels_global, 'data': data_global}
    })

def image_to_base64(image_field):
    if image_field and hasattr(image_field, 'path'):
        try:
            with open(image_field.path, "rb") as image_file:
                encoded_string = base64.b64encode(image_file.read()).decode('utf-8')
                # Obtener la extensión para armar el data URI
                ext = image_field.name.split('.')[-1].lower()
                mime_type = f"image/{ext}" if ext in ['png', 'jpg', 'jpeg', 'svg'] else "image/png"
                return f"data:{mime_type};base64,{encoded_string}"
        except Exception:
            return None
    return None

@login_required
@rol_requerido(['medico'])
def generar_constancia(request, paciente_id):
    paciente = get_object_or_404(Paciente, id=paciente_id)
    
    try:
        medico = request.user.medico
    except AttributeError:
        messages.error(request, "Debe ser un médico registrado para emitir constancias.")
        return redirect('dashboard_medico')

    if request.method == 'POST':
        motivo_texto = request.POST.get('motivo_texto', '').strip()
        
        if motivo_texto:
            constancia = ConstanciaMedica.objects.create(
                paciente=paciente,
                medico=medico,
                motivo_texto=motivo_texto
            )
            
            # --- CORRECCIÓN: Usamos exactamente los nombres de tus campos ---
            firma_b64 = image_to_base64(medico.firma)
            sello_b64 = image_to_base64(medico.sello)
            # ---------------------------------------------------------------
            
            context = {
                'constancia': constancia,
                'paciente': paciente,
                'medico': medico,
                'firma_b64': firma_b64, 
                'sello_b64': sello_b64,
            }
            return render(request, 'medico/constancia_pdf.html', context)
        else:
            messages.error(request, "El texto de la constancia no puede estar vacío.")

    return redirect('ver_expediente', paciente_id=paciente.id)

@login_required
@rol_requerido(['medico'])
def exportar_morbilidad_excel(request):
    tipo = request.GET.get('tipo', 'personal')
    hoy = timezone.now().date()
    
    try:
        medico_perfil = request.user.medico
    except AttributeError:
        messages.error(request, "Perfil no autorizado.")
        return redirect('landing_page')
    
    # 1. Filtramos los datos ignorando los diagnósticos vacíos
    query = ConsultaEvolucion.objects.exclude(diagnostico__isnull=True).exclude(diagnostico__exact='')
    
    if tipo == 'personal':
        query = query.filter(medico=medico_perfil)
        titulo_reporte = f'Morbilidad Personal - Dr(a). {request.user.last_name}'
        color_grafico = '#2563EB' # Azul
    else:
        titulo_reporte = 'Morbilidad General del Ambulatorio'
        color_grafico = '#DC2626' # Rojo Cruz Roja
        
    # Agrupamos por diagnóstico, contamos los casos y sacamos el Top 10
    top_diagnosticos = query.values('diagnostico').annotate(
        total=Count('id')
    ).order_by('-total')[:10]
    
    # 2. Preparamos el Excel en Memoria
    output = io.BytesIO()
    workbook = xlsxwriter.Workbook(output, {'in_memory': True})
    worksheet = workbook.add_worksheet('Morbilidad')
    
    # Formatos de diseño
    formato_titulo = workbook.add_format({'bold': True, 'font_size': 14, 'font_color': 'white', 'bg_color': color_grafico, 'align': 'center', 'valign': 'vcenter'})
    formato_cabecera = workbook.add_format({'bold': True, 'bg_color': '#E5E7EB', 'border': 1})
    formato_celda = workbook.add_format({'border': 1})
    
    worksheet.set_column('A:A', 45) # Columna ancha para diagnósticos largos
    worksheet.set_column('B:B', 15)
    
    worksheet.merge_range('A1:B2', titulo_reporte, formato_titulo)
    worksheet.write('A4', 'Diagnóstico Clínico (CIE-10)', formato_cabecera)
    worksheet.write('B4', 'N° de Casos', formato_cabecera)
    
    fila = 4
    for item in top_diagnosticos:
        worksheet.write(fila, 0, item['diagnostico'], formato_celda)
        worksheet.write(fila, 1, item['total'], formato_celda)
        fila += 1
        
    # 3. ¡LA MAGIA! Gráfico de Barras Horizontales
    if len(top_diagnosticos) > 0:
        chart = workbook.add_chart({'type': 'bar'}) # Usamos 'bar' en vez de 'column' para leer mejor los nombres
        chart.add_series({
            'name': 'Casos Registrados',
            'categories': ['Morbilidad', 4, 0, fila - 1, 0],
            'values':     ['Morbilidad', 4, 1, fila - 1, 1],
            'fill':       {'color': color_grafico}
        })
        chart.set_title({'name': 'Top 10 Diagnósticos Frecuentes'})
        chart.set_legend({'none': True})
        worksheet.insert_chart('D4', chart, {'x_scale': 1.5, 'y_scale': 1.2})
        
    workbook.close()
    output.seek(0)
    
    response = HttpResponse(
        output.read(), 
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = f'attachment; filename="Morbilidad_{tipo.capitalize()}_{hoy}.xlsx"'
    
    return response

