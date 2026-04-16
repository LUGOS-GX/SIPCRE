from django.shortcuts import render, redirect
from django.contrib.auth import login, authenticate, logout
from django.db import transaction 
from django.contrib import messages
from .forms import RegistroAdminForm, RegistroMedicoForm, LoginUsuarioForm, RegistroLaboratorioForm, RegistroFarmaciaForm
from administracion.models import Medico
from .models import Usuario


# 1. LANDING PAGE (Página Principal)
def landing_page(request):
    if request.user.is_authenticated:
        redireccion = redirigir_segun_rol(request.user)

        if redireccion:
            return redireccion
    return render(request, 'usuarios/landing.html')

def seleccion_rol(request):
    return render(request, 'usuarios/seleccion_rol.html')

def redirigir_segun_rol(user):
    if user.is_superuser:
        return redirect('/admin_django/')

    if user.rol == 'medico':
        return redirect('dashboard_medico')
    elif user.rol == 'admin':
        return redirect('dashboard_admin')
    elif user.rol == 'farmacia':
        return redirect('dashboard_farmacia')
    elif user.rol == 'laboratorio':
        return redirect('dashboard_lab')
    return None

# REGISTRO MÉDICO (Se mantiene separado por su complejidad)
@transaction.atomic
def registro_medico(request):
    if request.method == 'POST':
        form = RegistroMedicoForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                # 1. Guardar Usuario CONGELADO (is_active = False)
                user = form.save(commit=False)
                user.is_active = False
                user.save()

                # 2. Crear Ficha en Administración
                Medico.objects.create(
                    usuario=user,
                    nombre=f"{user.first_name} {user.last_name}",
                    especialidad=user.especialidad or "Medicina General", 
                    telefono=user.telefono or "Sin teléfono",
                    email=user.email,
                )
                
                # 3. Mensaje de Sala de Espera y redirección al login
                messages.success(request, 'Registro exitoso. Su cuenta ha sido enviada a Recursos Humanos para su aprobación. Recibirá un correo cuando sea habilitada.')
                return redirect('login')

            except Exception as e:
                # Si algo falla, el @transaction.atomic deshace todo automáticamente.
                messages.error(request, f"Error al crear el perfil médico: {str(e)}")
                print(f"ERROR CRÍTICO EN REGISTRO: {e}") 
    else:
        form = RegistroMedicoForm()
    return render(request, 'usuarios/registro_medico.html', {'form': form})

# REGISTRO UNIFICADO (Admin, Farmacia, Lab)
def registro_personal(request, rol_solicitado):
    # Diccionario maestro: Mapea la URL al Formulario correcto y al Título a mostrar
    roles_permitidos = {
        'admin': {
            'nombre': 'Administración',
            'form_class': RegistroAdminForm
        },
        'farmacia': {
            'nombre': 'Farmacia',
            'form_class': RegistroFarmaciaForm
        },
        'laboratorio': {
            'nombre': 'Laboratorio Clínico',
            'form_class': RegistroLaboratorioForm
        }
    }

    # Si intentan poner un rol inventado en la URL, los devolvemos a seleccionar rol
    if rol_solicitado not in roles_permitidos:
        return redirect('seleccion_rol')

    # Extraemos la configuración del rol solicitado
    info_rol = roles_permitidos[rol_solicitado]
    FormularioDinamico = info_rol['form_class']

    if request.method == 'POST':
        # Instanciamos el formulario que corresponda según el diccionario
        form = FormularioDinamico(request.POST)
        if form.is_valid():
            # Guardar Usuario CONGELADO
            user = form.save(commit=False)
            user.is_active = False
            user.save()
            
            messages.success(request, 'Registro exitoso. Su cuenta ha sido enviada a Recursos Humanos para su aprobación. Recibirá un correo cuando sea habilitada.')
            return redirect('login')
        else:
            messages.error(request, "Error en el registro. Verifique los datos ingresados.")
    else:
        form = FormularioDinamico()

    # Enviamos la data al template unificado
    context = {
        'form': form,
        'rol_db': rol_solicitado,
        'rol_mostrar': info_rol['nombre']
    }
    
    return render(request, 'usuarios/registro_personal.html', context)

#Login
def login_view(request):
    if request.method == 'POST':
        form = LoginUsuarioForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            return redirigir_segun_rol(user)
    else:
        form = LoginUsuarioForm()
    return render(request, 'usuarios/login.html', {'form': form})

#logout
def logout_view(request):
    logout(request)
    return redirect('landing_page')
