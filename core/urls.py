from django.contrib import admin
from django.urls import path
from entregas.views import relatorio_entregas, consulta_publica, home
from entregas.views import gerenciar_palavras

urlpatterns = [
    # Rota Raiz (Home Page)
    path('', home, name='home'),
    
    # Rota de Consulta Pública
    path('consulta/', consulta_publica, name='consulta_publica'),

    # Rotas do Admin (Restritas)
    path('admin/relatorio/', relatorio_entregas, name='relatorio_entregas'),
    
    path('admin/gerenciar-palavras/', gerenciar_palavras, name='gerenciar_palavras'),
    
    path('admin/', admin.site.urls),
]