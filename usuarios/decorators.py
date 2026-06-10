from functools import wraps
from django.core.exceptions import PermissionDenied
from django.contrib.auth.views import redirect_to_login


def rol_requerido(roles_permitidos):
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            # 1. Si no ha iniciado sesión, lo mandamos al login (no un 403 seco).
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path())

            rol_usuario = getattr(request.user, 'rol', None)

            # 2. Rol correcto o superusuario: pasa.
            if rol_usuario in roles_permitidos or request.user.is_superuser:
                return view_func(request, *args, **kwargs)

            # 3. Autenticado pero sin permiso -> 403 (fuga de rol).
            raise PermissionDenied

        return _wrapped_view
    return decorator
