from django import forms
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm
from django.core.exceptions import ValidationError
from .models import Usuario

#---MIXIN DE ESTILOS ---
class EstiloFormMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields:
            if not isinstance(self.fields[field].widget, forms.Select):
                self.fields[field].widget.attrs.update({'class': 'w-full border border-gray-300 rounded px-3 py-2 focus:outline-none focus:border-cruzroja-500 transition'})
            else:
                 self.fields[field].widget.attrs.update({'class': 'w-full border border-gray-300 rounded px-3 py-2 bg-white'})

#---MIXIN DE LÓGICA (Solo métodos, no campos)---
class LogicaDatosPersonalesMixin:
    def clean_cedula_numero(self):
        numero = self.cleaned_data.get('cedula_numero')
        if not numero.isdigit():
             raise ValidationError("La cédula debe contener solo números.")
        if len(numero) < 6 or len(numero) > 9:
             raise ValidationError("La cédula debe tener entre 6 y 9 dígitos.")
        return numero

    def clean_telefono_numero(self):
        numero = self.cleaned_data.get('telefono_numero')
        if not numero.isdigit():
            raise ValidationError("El teléfono debe contener solo números.")
        if len(numero) != 7:
            raise ValidationError("El número debe tener exactamente 7 dígitos.")
        return numero

    def save_datos_personales(self, user):
        # Unir Cédula
        nacionalidad = self.cleaned_data['nacionalidad']
        cedula_num = self.cleaned_data['cedula_numero']
        user.cedula = f"{nacionalidad}-{cedula_num}"

        # Unir Teléfono
        prefijo = self.cleaned_data['codigo_area']
        telf_num = self.cleaned_data['telefono_numero']
        user.telefono = f"{prefijo}-{telf_num}"
        return user

    def clean(self):
        # 1. Llamamos al clean original para no perder las validaciones base de Django
        cleaned_data = super().clean()
        
        # 2. Capturamos lo que el usuario escribió
        nacionalidad = cleaned_data.get('nacionalidad')
        cedula_numero = cleaned_data.get('cedula_numero')

        # 3. Si escribió ambos datos, verificamos antes de guardar
        if nacionalidad and cedula_numero:
            cedula_completa = f"{nacionalidad}-{cedula_numero}"
            # Consultamos si ya existe alguien con esa cédula en la BD
            if Usuario.objects.filter(cedula=cedula_completa).exists():
                self.add_error('cedula_numero', f'La cédula {cedula_completa} ya se encuentra registrada en el sistema.')
                
        # 4. También puedes hacer lo mismo para el teléfono si quieres que sea único
        codigo_area = cleaned_data.get('codigo_area')
        telefono_numero = cleaned_data.get('telefono_numero')
        
        if codigo_area and telefono_numero:
            telefono_completo = f"{codigo_area}-{telefono_numero}"
            if Usuario.objects.filter(telefono=telefono_completo).exists():
                self.add_error('telefono_numero', 'Este número de teléfono ya está registrado.')

        return cleaned_data

#---CONSTANTES---
OPCIONES_NACIONALIDAD = [('V', 'V'), ('E', 'E')]
OPCIONES_PREFIJO = [
    ('0414', '0414'), ('0424', '0424'),
    ('0412', '0412'), ('0416', '0416'),
    ('0426', '0426'), ('0422', '0422')
]

#FORMULARIO ADMINISTRACIÓN
class RegistroAdminForm(EstiloFormMixin, LogicaDatosPersonalesMixin, UserCreationForm):
    nacionalidad = forms.ChoiceField(choices=OPCIONES_NACIONALIDAD, label="Nac.")
    cedula_numero = forms.CharField(label="Número de Cédula", widget=forms.TextInput(attrs={'placeholder': 'Ej. 12345678'}))
    
    codigo_area = forms.ChoiceField(choices=OPCIONES_PREFIJO, label="Prefijo")
    telefono_numero = forms.CharField(label="Número", widget=forms.TextInput(attrs={'placeholder': '1234567', 'maxlength': '7'}))

    class Meta:
        model = Usuario
        fields = ('email', 'first_name', 'last_name')
    
    def save(self, commit=True):
        user = super().save(commit=False)
        user.username = user.email
        user.rol = 'admin'
        user = self.save_datos_personales(user)
        if commit:
            user.save()
        return user

#FORMULARIO MÉDICO
class RegistroMedicoForm(EstiloFormMixin, LogicaDatosPersonalesMixin, UserCreationForm):
    # --- CAMPOS EXPLÍCITOS AQUÍ ---
    nacionalidad = forms.ChoiceField(choices=OPCIONES_NACIONALIDAD, label="Nac.")
    cedula_numero = forms.CharField(label="Número de Cédula", widget=forms.TextInput(attrs={'placeholder': 'Ej. 12345678'}))
    
    codigo_area = forms.ChoiceField(choices=OPCIONES_PREFIJO, label="Prefijo")
    telefono_numero = forms.CharField(label="Número", widget=forms.TextInput(attrs={'placeholder': '1234567', 'maxlength': '7'}))

    cm = forms.CharField(
        label="Colegio Médico (CM)", 
        required=True, 
        widget=forms.TextInput(attrs={'placeholder': 'Ej. 12345'})
    )

    class Meta:
        model = Usuario
        fields = ('email', 'first_name', 'last_name', 'mpps','cm', 'especialidad')

    def save(self, commit=True):
        user = super().save(commit=False)
        user.username = user.email
        user.rol = 'medico'
        user = self.save_datos_personales(user)
        if commit:
            user.save()
        return user

#FORMULARIO PARA LABORATORIO
class RegistroLaboratorioForm(EstiloFormMixin, LogicaDatosPersonalesMixin, UserCreationForm):
    # Campos base para que el admin llene la info del bioanalista o del laboratorio
    nacionalidad = forms.ChoiceField(choices=OPCIONES_NACIONALIDAD, label="Nac.")
    cedula_numero = forms.CharField(label="Cédula / RIF", widget=forms.TextInput(attrs={'placeholder': 'Ej. 12345678'}))
    
    codigo_area = forms.ChoiceField(choices=OPCIONES_PREFIJO, label="Prefijo")
    telefono_numero = forms.CharField(label="Número de Teléfono", widget=forms.TextInput(attrs={'placeholder': '1234567', 'maxlength': '7'}))

    class Meta:
        model = Usuario
        # Solo pedimos los datos esenciales de la cuenta
        fields = ('email', 'first_name', 'last_name')

    def save(self, commit=True):
        user = super().save(commit=False)
        # Usamos el email como nombre de usuario para el login
        user.username = user.email
        # AQUÍ ASIGNAMOS EL ROL EXACTO QUE TIENES EN TU BD
        user.rol = 'laboratorio' 
        
        user = self.save_datos_personales(user)
        if commit:
            user.save()
        return user

#FORMULARIO FARMACIA
class RegistroFarmaciaForm(EstiloFormMixin, LogicaDatosPersonalesMixin, UserCreationForm):
    # Campos separados para aprovechar el Mixin y guardar la BD limpia
    nacionalidad = forms.ChoiceField(choices=OPCIONES_NACIONALIDAD, label="Nac.")
    cedula_numero = forms.CharField(label="Cédula", widget=forms.TextInput(attrs={'placeholder': 'Ej. 12345678'}))
    
    codigo_area = forms.ChoiceField(choices=OPCIONES_PREFIJO, label="Prefijo")
    telefono_numero = forms.CharField(label="Número de Teléfono", widget=forms.TextInput(attrs={'placeholder': '1234567', 'maxlength': '7'}))

    class Meta:
        model = Usuario
        # Solo pedimos los esenciales; los mixins hacen el resto
        fields = ('email', 'first_name', 'last_name')

    def save(self, commit=True):
        user = super().save(commit=False)
        # Usamos el email como nombre de usuario para el login
        user.username = user.email
        # Asignamos el rol exacto de la base de datos
        user.rol = 'farmacia' 
        
        # El Mixin une la V- con la cédula y el 0414 con el teléfono
        user = self.save_datos_personales(user)
        
        if commit:
            user.save()
        return user

#LOGIN
class LoginUsuarioForm(AuthenticationForm):
    username = forms.CharField(
        label="Correo Electrónico", 
        widget=forms.TextInput(attrs={'class': 'w-full border border-gray-300 rounded px-3 py-2 focus:outline-none focus:border-cruzroja-500'})
    )
    password = forms.CharField(
        label="Contraseña",
        widget=forms.PasswordInput(attrs={'class': 'w-full border border-gray-300 rounded px-3 py-2 focus:outline-none focus:border-cruzroja-500'})
    )