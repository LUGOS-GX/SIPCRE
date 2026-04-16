from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.core.mail import send_mail
from django.conf import settings
from django.db.models import Q
from .models import Paciente, Cita, Medico, Factura, DetalleFactura
from usuarios.decorators import rol_requerido
from usuarios.models import Usuario


@login_required
@rol_requerido(['admin'])
def dashboard_admin(request):
    # 1. Obtenemos fecha actual Vzla por defecto
    hoy = timezone.localtime(timezone.now()).date()
    fecha_filtro_str = request.GET.get('fecha')

    if fecha_filtro_str:
        fecha_filtro = fecha_filtro_str 
    else:
        # Si no pidió nada, usamos HOY, pero convertido a String 'YYYY-MM-DD'
        fecha_filtro = hoy.strftime('%Y-%m-%d')

    # 3. Consulta
    citas = Cita.objects.filter(fecha=fecha_filtro).order_by('hora')

    context = {
        'citas': citas,
        'fecha_actual': fecha_filtro, 
        'hoy': hoy
    }
    return render(request, 'administracion/dashboard.html', context)

@login_required
@rol_requerido(['admin'])
def agendar_cita(request):
    # Obtenemos todos los médicos para el desplegable
    medicos = Medico.objects.all()

    if request.method == 'POST':
        # Datos del Paciente
        nombre = request.POST.get('nombre')
        cedula_num = request.POST.get('cedula')
        nacionalidad = request.POST.get('nacionalidad')
        fecha_cita = request.POST.get('fecha_cita')
        hora_cita = request.POST.get('hora_cita') 
        
        if not hora_cita:
            hora_cita = '08:00'

        medico_id = request.POST.get('medico')
        motivo = request.POST.get('motivo')
        costo_consulta = request.POST.get('costo_consulta')
        opcion_pago = request.POST.get('opcion_pago')
        metodo_pago = request.POST.get('metodo_pago')

        # VALIDACIÓN 1: Verificar Cupo Disponible
        medico_obj = Medico.objects.get(id=medico_id)
        citas_existentes = Cita.objects.filter(medico=medico_obj, fecha=fecha_cita).count()

        if citas_existentes >= medico_obj.cupo_diario:
            # ERROR: No hay cupo
            messages.error(request, f"El {medico_obj.nombre} ya no tiene cupos para el {fecha_cita}.")
            return render(request, 'administracion/agendar_cita.html', {'medicos': medicos})

        paciente, _ = Paciente.objects.get_or_create(
            cedula=cedula_num,
            defaults={'nacionalidad': nacionalidad, 'nombres': nombre, 'tipo_sangre': 'O+'} # Simplificado
        )

        # Crear Cita
        Cita.objects.create(
            paciente=paciente, # (asumiendo que ya buscaste/creaste al paciente arriba)
            medico=medico_obj,
            fecha=fecha_cita,
            hora=hora_cita, 
            motivo=motivo
        )
        
        #logica de pago
        if costo_consulta:
            #1.Buscamos si el paciente ya tiene una "Cuenta Abierta", si no, la creamos
            factura, creada = Factura.objects.get_or_create(
                paciente=paciente,
                estado='Pendiente'
            )
            
            #2.Añadimos el costo de la consulta a esa cuenta
            DetalleFactura.objects.create(
                factura=factura,
                departamento='Consulta',
                descripcion=f"Consulta Médica - {medico_obj.especialidad} ({medico_obj.nombre})",
                cantidad=1,
                precio_unitario=float(costo_consulta)
            )
            
            #3.Verificamos si decidió pagar de una vez
            if opcion_pago == 'pagar_ahora' and metodo_pago:
                factura.estado = 'Pagada'
                factura.metodo_pago = metodo_pago
                factura.fecha_pago = timezone.now()
                factura.save()
                messages.success(request, f"Cita agendada y pagada exitosamente ({metodo_pago}).")
            else:
                messages.success(request, "Cita agendada. El cobro fue enviado a la Caja Central (Cuenta Abierta).")
        else:
            messages.success(request, "Cita agendada exitosamente.")

        return redirect('dashboard_admin')
        
    return render(request, 'administracion/agendar_cita.html', {'medicos': medicos})

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
    # Buscamos la cita o devolvemos error 404 si no existe
    cita = get_object_or_404(Cita, id=id_cita)
    medicos = Medico.objects.all()

    if request.method == 'POST':
        # Actualizamos los campos
        cita.fecha = request.POST.get('fecha_cita')
        cita.hora = request.POST.get('hora_cita')
        cita.motivo = request.POST.get('motivo')
        
        # Actualizar médico
        medico_id = request.POST.get('medico')
        if medico_id:
            cita.medico = Medico.objects.get(id=medico_id)
        
        # Actualizar estado (por si acaso quieres cambiarlo manual)
        cita.estado = request.POST.get('estado')
        
        cita.save()
        
        messages.success(request, "Cita modificada correctamente.")
        # Redirigimos al dashboard con la fecha de la cita para ver el cambio
        return redirect(f'/administracion/?fecha={cita.fecha}')

    context = {
        'cita': cita,
        'medicos': medicos,
        # Formateamos la hora para que el select la reconozca (HH:MM)
        'hora_actual': cita.hora.strftime('%H:%M') if cita.hora else '' 
    }
    return render(request, 'administracion/editar_cita.html', context)


@login_required
@rol_requerido(['admin'])
def caja_central(request):
    query = request.GET.get('buscar_cedula', '').strip()

    # Traemos TODAS las facturas pendientes
    facturas_pendientes = Factura.objects.filter(estado='Pendiente').order_by('-fecha_emision')

    # Si se escribió algo en el buscador, buscamos en los pacientes registrados O en los de paso
    if query:
        facturas_pendientes = facturas_pendientes.filter(
            Q(paciente__cedula__icontains=query) | Q(cedula_cliente__icontains=query)
        )
        
        if not facturas_pendientes.exists():
            messages.warning(request, f"No se encontró ninguna factura pendiente para la cédula '{query}'.")

    # Procesamiento del Pago
    if request.method == 'POST':
        accion = request.POST.get('accion')
        
        if accion == 'procesar_pago':
            factura_id = request.POST.get('factura_id')
            metodo_pago = request.POST.get('metodo_pago')
            
            if factura_id and metodo_pago:
                factura = get_object_or_404(Factura, id=factura_id)
                factura.estado = 'Pagada'
                factura.metodo_pago = metodo_pago
                factura.fecha_pago = timezone.now()
                factura.save()
                messages.success(request, f"¡Pago procesado! Factura {factura.numero_factura} pagada mediante {metodo_pago}.")
                
        elif accion == 'agregar_cargo':
            factura_id = request.POST.get('factura_id')
            departamento = request.POST.get('departamento')
            descripcion = request.POST.get('descripcion')
            precio = request.POST.get('precio')
            
            if factura_id and descripcion and precio:
                factura = get_object_or_404(Factura, id=factura_id)
                DetalleFactura.objects.create(
                    factura=factura,
                    departamento=departamento,
                    descripcion=descripcion,
                    cantidad=1,
                    precio_unitario=float(precio)
                )
                messages.success(request, f"Cargo de '{descripcion}' añadido a la cuenta.")

        return redirect('caja_central')

    contexto = {
        'query': query,
        'facturas_pendientes': facturas_pendientes,
    }
    return render(request, 'administracion/caja_central.html', contexto)

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
