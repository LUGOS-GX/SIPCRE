import requests
from decimal import Decimal

def obtener_tasa_bcv():
    """
    Se conecta a la API de DolarApi (Venezuela) para obtener la tasa oficial del BCV.
    Retorna un objeto Decimal con la tasa, o None si hay error de conexión.
    """
    try:
        # Endpoint oficial de la API que proporcionaste
        url = "https://ve.dolarapi.com/v1/dolares/oficial"
        response = requests.get(url, timeout=10) # 10 segundos de espera máximo
        
        if response.status_code == 200:
            data = response.json()
            # La API devuelve 'promedio', 'compra' y 'venta'. Usamos promedio o venta.
            tasa = data.get('promedio', data.get('venta'))
            return Decimal(str(tasa))
        return None
    except requests.RequestException:
        # Si no hay internet o falla la API, retornamos None para que el cajero la ponga manual
        return None