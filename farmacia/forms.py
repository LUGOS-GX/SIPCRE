from django import forms
from .models import Medicamento, LoteMedicamento

class MedicamentoForm(forms.ModelForm):
    class Meta:
        model = Medicamento
        fields = ['nombre', 'concentracion', 'presentacion', 'descripcion', 'foto', 'stock_actual', 'stock_minimo', 'precio', 'fecha_vencimiento', 'es_controlado']
        widgets = {
            'fecha_vencimiento': forms.DateInput(attrs={'type': 'date'}),
            'descripcion': forms.Textarea(attrs={'rows': 3, 'placeholder': 'Ej. Laboratorios Pfizer. Indicado para...'}),
        }

class LoteMedicamentoForm(forms.ModelForm):
    class Meta:
        model = LoteMedicamento
        fields = ['medicamento', 'numero_lote', 'cantidad_ingresada', 'fecha_vencimiento']
        widgets = {
            'fecha_vencimiento': forms.DateInput(attrs={'type': 'date'}),
            # Usamos select2 o clases de tailwind si prefieres, por ahora lo dejamos estandar
        }
        labels = {
            'medicamento': 'Seleccione el Medicamento',
            'cantidad_ingresada': 'Cantidad de Cajas/Unidades',
        }