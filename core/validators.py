import imghdr
from django.core.exceptions import ValidationError


# Tipos de archivo permitidos por categoría
TIPOS_IMAGEN = ['png', 'jpeg', 'jpg', 'webp']
TIPOS_DOCUMENTO = ['pdf', 'doc', 'docx', 'xls', 'xlsx']
TIPOS_IMAGEN_Y_PDF = TIPOS_IMAGEN + TIPOS_DOCUMENTO

# Tamaño máximo: 5 MB
TAMANO_MAXIMO_MB = 5
TAMANO_MAXIMO_BYTES = TAMANO_MAXIMO_MB * 1024 * 1024


def validar_imagen(archivo):
    """
    Valida que el archivo sea realmente una imagen (PNG, JPEG, WEBP).
    Verifica el contenido real del archivo, no solo la extensión.
    """
    # 1. Verificar tamaño
    if archivo.size > TAMANO_MAXIMO_BYTES:
        raise ValidationError(f'El archivo no puede superar {TAMANO_MAXIMO_MB} MB.')

    # 2. Verificar tipo real leyendo los primeros bytes (magic bytes)
    tipo_real = imghdr.what(archivo)
    archivo.seek(0)  # Rebobinar para que Django pueda seguir leyendo

    if tipo_real not in TIPOS_IMAGEN:
        raise ValidationError(
            f'Tipo de archivo no permitido. Solo se aceptan: {", ".join(TIPOS_IMAGEN).upper()}.'
        )


def validar_imagen_o_pdf(archivo):
    """
    Valida que el archivo sea una imagen o un PDF.
    Usado para resultados de laboratorio y documentos médicos.
    """
    # 1. Verificar tamaño
    if archivo.size > TAMANO_MAXIMO_BYTES:
        raise ValidationError(f'El archivo no puede superar {TAMANO_MAXIMO_MB} MB.')

    # 2. Leer los primeros bytes para detectar el tipo real
    cabecera = archivo.read(8)
    archivo.seek(0)

    # Verificar si es PDF (empieza con %PDF)
    if cabecera[:4] == b'%PDF':
        return

    # Verificar si es imagen válida
    import io
    tipo_real = imghdr.what(io.BytesIO(cabecera))
    if tipo_real not in TIPOS_IMAGEN:
        raise ValidationError(
            f'Tipo de archivo no permitido. Solo se aceptan imágenes o PDF.'
        )
