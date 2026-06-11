import requests
from decimal import Decimal, InvalidOperation


def obtener_tasa_bcv():
    """
    Se conecta a la API de DolarApi (Venezuela) para obtener la tasa oficial del BCV.
    Retorna un objeto Decimal con la tasa, o None si hay error de conexión o si
    la API responde con datos nulos/malformados (en ese caso la vista decide
    el respaldo: última tasa registrada o ingreso manual).
    """
    try:
        url = "https://ve.dolarapi.com/v1/dolares/oficial"
        response = requests.get(url, timeout=10)  # 10 segundos de espera máximo

        if response.status_code != 200:
            return None

        data = response.json()
        # 'or' (y no .get con default) para cubrir el caso {"promedio": null, ...}
        tasa = data.get('promedio') or data.get('venta')
        if tasa is None:
            return None

        tasa_decimal = Decimal(str(tasa))
        if tasa_decimal <= 0:
            return None
        return tasa_decimal

    except (requests.RequestException, ValueError, KeyError, InvalidOperation):
        # Sin internet, JSON inválido o número corrupto: el cajero la pone manual.
        return None
