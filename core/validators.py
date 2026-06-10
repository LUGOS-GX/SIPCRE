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
