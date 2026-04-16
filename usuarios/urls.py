from django.urls import path
from . import views

urlpatterns = [
    path('', views.landing_page, name='landing_page'),
    path('registro/seleccion/', views.seleccion_rol, name='seleccion_rol'),
    path('registro/medico/', views.registro_medico, name='registro_medico'),
    path('registro/personal/<str:rol_solicitado>/', views.registro_personal, name='registro_personal'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
]