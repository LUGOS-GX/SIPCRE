from functools import wraps
from django.core.exceptions import PermissionDenied

def rol_requerido(roles_permitidos):
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            rol_usuario = getattr(request.user, 'rol', None)
            
            # 1. Si está autenticado y tiene el rol correcto, pasa.
            if request.user.is_authenticated and rol_usuario in roles_permitidos:
                return view_func(request, *args, **kwargs)
            
            # 2. Si es superusuario (yo), pasa a revisar.
            if request.user.is_superuser:
                return view_func(request, *args, **kwargs)

            # 3. Si llega aquí, es porque está intentando hacer una FUGA DE ROL.
            # Disparamos el error 403 automáticamente.
            raise PermissionDenied
                
        return _wrapped_view
    return decorator