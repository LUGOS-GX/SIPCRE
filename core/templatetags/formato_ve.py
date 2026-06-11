"""
Filtros de presentación venezolanos.
Las cédulas se ALMACENAN canónicas (solo dígitos); estos filtros son
exclusivamente visuales para mostrarlas con separadores de miles.
"""
from django import template

register = template.Library()


@register.filter
def cedula_puntos(valor):
    """
    '12345678'    -> '12.345.678'
    'V-12345678'  -> '12.345.678'  (tolera datos viejos sin normalizar)
    '1.234.567'   -> '1.234.567'   (re-formatea uniforme)
    Si no hay dígitos, devuelve el valor tal cual.
    """
    digitos = ''.join(ch for ch in str(valor) if ch.isdigit())
    if not digitos:
        return valor
    return '{:,}'.format(int(digitos)).replace(',', '.')
