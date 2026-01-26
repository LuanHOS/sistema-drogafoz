from django.shortcuts import render
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Sum, Count, Avg, Min, Max, Q
from django.utils import timezone
from datetime import timedelta
from .models import Encomenda, Cliente

# --- RELATÓRIO ADMINISTRATIVO ---
@staff_member_required
def relatorio_entregas(request):
    data_final = request.GET.get('data_final', timezone.now().strftime('%Y-%m-%d'))
    data_inicial = request.GET.get('data_inicial', (timezone.now() - timedelta(days=30)).strftime('%Y-%m-%d'))

    # Filtra as encomendas baseadas na data de chegada
    encomendas = Encomenda.objects.filter(
        data_chegada__date__range=[data_inicial, data_final]
    )

    # 1. Métricas de Valor (Dinheiro)
    # Total já recebido (Encomendas Entregues)
    total_ganhos = encomendas.filter(status='ENTREGUE').aggregate(Sum('valor_cobrado'))['valor_cobrado__sum'] or 0
    
    # NOVO: Total a receber (Encomendas paradas no estoque)
    ganhos_pendentes = encomendas.filter(status='PENDENTE').aggregate(Sum('valor_cobrado'))['valor_cobrado__sum'] or 0

    # 2. Métricas de Quantidade
    total_encomendas = encomendas.count()
    total_entregues = encomendas.filter(status='ENTREGUE').count()
    total_pendentes = encomendas.filter(status='PENDENTE').count()

    # NOVO: Cálculo do Ticket Médio (Valor médio por pacote entregue)
    if total_entregues > 0:
        ticket_medio = total_ganhos / total_entregues
    else:
        ticket_medio = 0

    context = {
        'total_ganhos': total_ganhos,
        'ganhos_pendentes': ganhos_pendentes, # Novo dado
        'ticket_medio': ticket_medio,         # Novo dado
        'total_encomendas': total_encomendas,
        'total_entregues': total_entregues,
        'total_pendentes': total_pendentes,
        'data_inicial': data_inicial,
        'data_final': data_final,
        'site_header': 'DROGAFOZ ENCOMENDAS',
        'title': 'Relatório de Gestão',
    }
    
    return render(request, 'admin/relatorio_ganhos.html', context)

# --- CONSULTA PÚBLICA ---
def consulta_publica(request):
    query = request.GET.get('q')
    resultados = []

    if query:
        termo_limpo = query.replace('.', '').replace('-', '').strip()
        resultados = Cliente.objects.filter(
            Q(cpf=query) | Q(cpf=termo_limpo) | Q(rg=query),
            encomenda__status='PENDENTE'
        ).annotate(
            qtd_encomendas=Count('encomenda'),
            primeira_chegada=Min('encomenda__data_chegada'),
            ultima_chegada=Max('encomenda__data_chegada')
        ).filter(qtd_encomendas__gt=0)

    return render(request, 'publica/consulta.html', {'resultados': resultados, 'query': query})

def home(request):
    return render(request, 'publica/home.html')