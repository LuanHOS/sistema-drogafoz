from django.shortcuts import render, redirect
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Sum, Count, Avg, Min, Max, Q
from django.utils import timezone
from datetime import datetime, timedelta
from django.utils.timezone import make_aware
from .models import Encomenda, Cliente

# --- RELATÓRIO ADMINISTRATIVO ---
@staff_member_required
def relatorio_entregas(request):
    # 1. Captura inputs
    data_inicial_str = request.GET.get('data_inicial')
    data_final_str = request.GET.get('data_final')
    ignorar_periodo = request.GET.get('ignorar_periodo') == 'on'

    # 2. Definição de Datas Padrão (Se não vier nada)
    hoje = timezone.now()
    if not data_final_str:
        data_final_str = hoje.strftime('%Y-%m-%d')
    if not data_inicial_str:
        data_inicial_str = (hoje - timedelta(days=30)).strftime('%Y-%m-%d')

    # 3. Tratamento Robusto de Datas (Para corrigir o erro do "Mesmo Dia")
    # Converte string para objeto datetime consciente do fuso horário
    try:
        dt_inicial = make_aware(datetime.strptime(data_inicial_str, '%Y-%m-%d'))
        # Define a data final para o último segundo do dia (23:59:59)
        dt_final = make_aware(datetime.strptime(data_final_str, '%Y-%m-%d').replace(hour=23, minute=59, second=59))
    except ValueError:
        # Fallback caso a data venha quebrada
        dt_inicial = hoje - timedelta(days=30)
        dt_final = hoje

    # --- CONSULTAS ---
    
    qs_todas = Encomenda.objects.all()

    # BLOCO 1: DADOS GERAIS (Snapshot da Loja Hoje)
    # Não dependem do filtro de data. É o "Agora".
    # Usamos 'valor_base' para pendentes pois 'valor_cobrado' pode ser nulo agora.
    estoque_atual_qtd = qs_todas.filter(status='PENDENTE').count()
    estoque_atual_valor = qs_todas.filter(status='PENDENTE').aggregate(Sum('valor_base'))['valor_base__sum'] or 0

    # BLOCO 2: DADOS DO PERÍODO (Fluxo)
    if ignorar_periodo:
        # Se checkbox marcado, pega tudo desde o início
        encomendas_entregues = qs_todas.filter(status='ENTREGUE')
        encomendas_chegadas = qs_todas
        periodo_label = "Todo o Histórico"
    else:
        # Filtra pelo range ajustado (00:00 até 23:59)
        encomendas_entregues = qs_todas.filter(
            status='ENTREGUE',
            data_entrega__range=(dt_inicial, dt_final)
        )
        encomendas_chegadas = qs_todas.filter(
            data_chegada__range=(dt_inicial, dt_final)
        )
        periodo_label = f"{dt_inicial.strftime('%d/%m/%Y')} até {dt_final.strftime('%d/%m/%Y')}"

    # Cálculos do Período
    faturamento_periodo = encomendas_entregues.aggregate(Sum('valor_cobrado'))['valor_cobrado__sum'] or 0
    qtd_entregues_periodo = encomendas_entregues.count()
    qtd_chegadas_periodo = encomendas_chegadas.count()

    if qtd_entregues_periodo > 0:
        ticket_medio = faturamento_periodo / qtd_entregues_periodo
    else:
        ticket_medio = 0

    context = {
        # Filtros para manter no formulário
        'data_inicial': data_inicial_str,
        'data_final': data_final_str,
        'ignorar_periodo': ignorar_periodo,
        'periodo_label': periodo_label,

        # Bloco Geral (Estático)
        'estoque_atual_qtd': estoque_atual_qtd,
        'estoque_atual_valor': estoque_atual_valor,

        # Bloco Período (Dinâmico)
        'faturamento_periodo': faturamento_periodo,
        'qtd_entregues_periodo': qtd_entregues_periodo,
        'qtd_chegadas_periodo': qtd_chegadas_periodo,
        'ticket_medio': ticket_medio,

        'site_header': 'DROGAFOZ ENCOMENDAS',
        'title': 'Relatório Financeiro e Operacional',
    }
    
    return render(request, 'admin/relatorio_ganhos.html', context)

# --- CONSULTA PÚBLICA (MANTIDA IGUAL) ---
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