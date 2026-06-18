import re
from django.core.exceptions import ValidationError
from PIL import Image

# Tipos de archivo permitidos por categoría
TIPOS_IMAGEN = ['png', 'jpeg', 'jpg', 'webp']
TIPOS_DOCUMENTO = ['pdf', 'doc', 'docx', 'xls', 'xlsx']
TIPOS_IMAGEN_Y_PDF = TIPOS_IMAGEN + TIPOS_DOCUMENTO

# Formatos que Pillow reporta en img.format para las imágenes que aceptamos
FORMATOS_PIL_PERMITIDOS = {'PNG', 'JPEG', 'WEBP'}

# Tamaño máximo: 5 MB
TAMANO_MAXIMO_MB = 5
TAMANO_MAXIMO_BYTES = TAMANO_MAXIMO_MB * 1024 * 1024


def _es_imagen_valida(archivo):
    """
    Verifica el contenido REAL del archivo (no la extensión) usando Pillow.
    Reemplaza a imghdr, que quedó deprecado y fue removido en Python 3.13,
    y que además nunca detectó correctamente el formato WEBP.
    """
    try:
        archivo.seek(0)
        with Image.open(archivo) as img:
            formato = img.format          # se lee antes de verify()
            img.verify()                  # valida la integridad de la imagen
    except Exception:
        return False
    finally:
        archivo.seek(0)                   # rebobinar para que Django siga leyendo
    return formato in FORMATOS_PIL_PERMITIDOS


def validar_imagen(archivo):
    """
    Valida que el archivo sea realmente una imagen (PNG, JPEG, WEBP).
    """
    if archivo.size > TAMANO_MAXIMO_BYTES:
        raise ValidationError(f'El archivo no puede superar {TAMANO_MAXIMO_MB} MB.')

    if not _es_imagen_valida(archivo):
        raise ValidationError(
            f'Tipo de archivo no permitido. Solo se aceptan: {", ".join(TIPOS_IMAGEN).upper()}.'
        )


def validar_imagen_o_pdf(archivo):
    """
    Valida que el archivo sea una imagen (PNG, JPEG, WEBP) o un PDF.
    Usado para resultados de laboratorio y documentos médicos.
    """
    if archivo.size > TAMANO_MAXIMO_BYTES:
        raise ValidationError(f'El archivo no puede superar {TAMANO_MAXIMO_MB} MB.')

    # Detectar PDF por su firma (%PDF)
    archivo.seek(0)
    cabecera = archivo.read(5)
    archivo.seek(0)
    if cabecera[:4] == b'%PDF':
        return

    # Si no es PDF, verificar que sea una imagen válida
    if not _es_imagen_valida(archivo):
        raise ValidationError(
            'Tipo de archivo no permitido. Solo se aceptan imágenes (PNG, JPEG, WEBP) o PDF.'
        )


# ============================================================
# NORMALIZACIÓN DE CÉDULAS (formato canónico de TODO el sistema)
# ============================================================
# Regla única: las cédulas de pacientes se guardan y se buscan
# SIEMPRE como solo dígitos (sin "V-", sin puntos, sin espacios).
# La nacionalidad vive en su propio campo cuando aplica.
# Cualquier vista que reciba una cédula del usuario o que la use
# para cruzar Factura.cedula_cliente / Paciente.cedula DEBE pasar
# por estas dos funciones.

# --- Rango aceptado para una cédula venezolana ---
# CEDULA_MINIMO: piso para descartar basura (0, 1, 11, 111...). 100.000 (6
#   dígitos) cubre incluso a pacientes mayores con cédulas antiguas, sin dejar
#   pasar valores absurdamente pequeños. Si necesitas admitir cédulas de 5
#   dígitos o menos, baja este número; si quieres exigir 7 dígitos, súbelo a
#   1_000_000. Es la ÚNICA perilla del piso.
# CEDULA_MAXIMO: tope acordado para cédulas venezolanas.
# CEDULA_MAX_DIGITOS: corta cadenas infladas con ceros a la izquierda
#   ('000000000000', '00000040000000', etc.). 40.000.000 tiene 8 dígitos,
#   así que nada legítimo supera esa longitud.
CEDULA_MINIMO = 100_000
CEDULA_MAXIMO = 40_000_000
CEDULA_MAX_DIGITOS = len(str(CEDULA_MAXIMO))  # 8


def normalizar_cedula(valor):
    """
    Convierte cualquier entrada ('V-12.345.678', ' 12345678 ', etc.)
    al formato canónico: un string de solo dígitos.
    Devuelve '' si no hay ningún dígito.
    """
    if not valor:
        return ''
    return ''.join(ch for ch in str(valor) if ch.isdigit())


def cedula_es_valida(cedula_normalizada):
    """
    Valida una cédula YA normalizada (solo dígitos):
    no vacía, numérica, sin exceso de dígitos (evita el inflado con ceros
    a la izquierda) y dentro del rango CEDULA_MINIMO..CEDULA_MAXIMO.
    Nunca lanza excepción: devuelve True/False.
    """
    if not cedula_normalizada or not cedula_normalizada.isdigit():
        return False
    # '000000000000' / '00000040000000' -> demasiados dígitos: fuera.
    if len(cedula_normalizada) > CEDULA_MAX_DIGITOS:
        return False
    valor = int(cedula_normalizada)
    return CEDULA_MINIMO <= valor <= CEDULA_MAXIMO


# ============================================================
# NORMALIZACIÓN Y VALIDACIÓN DE NOMBRES (comprador / paciente libre)
# ============================================================
# Para los nombres escritos a mano (p. ej. el comprador en la caja de
# farmacia o un paciente no registrado). Acota longitud y caracteres para
# que no entren cadenas infinitas ni símbolos raros (<, >, @, $, emojis...).

NOMBRE_MINIMO = 2
NOMBRE_MAXIMO = 60
# Letras latinas con acentos/ñ/ü, espacios y signos válidos en nombres
# compuestos: punto (abreviaturas), apóstrofo y guion. Nada más.
_NOMBRE_PERMITIDO = re.compile(r"^[A-Za-zÁÉÍÓÚÜÑáéíóúüñ .'\-]+$")


def normalizar_nombre(valor):
    """
    Recorta extremos y colapsa espacios repetidos a uno solo.
    Devuelve '' si no hay contenido.
    """
    if not valor:
        return ''
    return ' '.join(str(valor).split())


def nombre_es_valido(nombre_normalizado):
    """
    Valida un nombre YA normalizado: longitud entre NOMBRE_MINIMO y
    NOMBRE_MAXIMO, solo caracteres permitidos y al menos una letra real
    (descarta entradas hechas solo de signos o espacios).
    Nunca lanza excepción: devuelve True/False.
    """
    if not nombre_normalizado:
        return False
    if not (NOMBRE_MINIMO <= len(nombre_normalizado) <= NOMBRE_MAXIMO):
        return False
    if not _NOMBRE_PERMITIDO.match(nombre_normalizado):
        return False
    return any(ch.isalpha() for ch in nombre_normalizado)
