from django.shortcuts import render
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Sum, Count, Avg, F, Q, Min, Max
from django.utils import timezone
from datetime import datetime, timedelta
from django.utils.timezone import make_aware
from .models import Encomenda, Cliente
import json

@staff_member_required
def relatorio_entregas(request):
    # --- 1. CONFIGURAÇÃO DE DATAS ---
    data_inicial_str = request.GET.get('data_inicial')
    data_final_str = request.GET.get('data_final')
    ignorar_periodo = request.GET.get('ignorar_periodo') == 'on'

    hoje = timezone.now()
    
    if not data_final_str:
        data_final_str = hoje.strftime('%Y-%m-%d')
    
    if not data_inicial_str:
        data_inicial_str = hoje.replace(day=1).strftime('%Y-%m-%d')

    try:
        dt_inicial = make_aware(datetime.strptime(data_inicial_str, '%Y-%m-%d'))
        dt_final = make_aware(datetime.strptime(data_final_str, '%Y-%m-%d').replace(hour=23, minute=59, second=59))
    except ValueError:
        dt_inicial = hoje.replace(day=1)
        dt_final = hoje

    qs_todas = Encomenda.objects.all()

    # --- 2. DADOS DO PERÍODO ---
    if ignorar_periodo:
        # Se ignorar, pega tudo
        encomendas_entregues = qs_todas.filter(status='ENTREGUE') 
        encomendas_chegadas = qs_todas 
        periodo_label = "Todo o Histórico"
    else:
        # SAÍDAS
        encomendas_entregues = qs_todas.filter(status='ENTREGUE', data_entrega__range=(dt_inicial, dt_final))
        # CHEGADAS
        encomendas_chegadas = qs_todas.filter(data_chegada__range=(dt_inicial, dt_final))
        
        periodo_label = f"{dt_inicial.strftime('%d/%m/%Y')} até {dt_final.strftime('%d/%m/%Y')}"

    # --- CÁLCULOS FINANCEIROS (CORRIGIDO) ---
    faturamento_real = encomendas_entregues.aggregate(Sum('valor_cobrado'))['valor_cobrado__sum'] or 0
    
    # Lógica de Desconto Corrigida:
    # Soma individualmente a diferença (Calculado - Cobrado) apenas quando houve desconto.
    # Isso impede que encomendas com lucro (cobrado a mais) anulem os descontos na soma total.
    descontos_dados = encomendas_entregues.filter(
        valor_calculado__gt=F('valor_cobrado')
    ).aggregate(
        total_desconto=Sum(F('valor_calculado') - F('valor_cobrado'))
    )['total_desconto'] or 0
    
    qtd_entregues = encomendas_entregues.count() 
    qtd_chegadas = encomendas_chegadas.count()   
    
    ticket_medio = (faturamento_real / qtd_entregues) if qtd_entregues > 0 else 0

    # Tempo Médio Global
    media_timedelta = qs_todas.filter(status='ENTREGUE').aggregate(media=Avg(F('data_entrega') - F('data_chegada')))['media']
    tempo_medio_dias = media_timedelta.days if media_timedelta else 0

    # Top 5 Clientes
    top_clientes = encomendas_entregues.values('cliente__nome') \
        .annotate(total_gasto=Sum('valor_cobrado'), qtd=Count('id')) \
        .order_by('-total_gasto')[:5]

    # Auditoria
    entregas_zeradas = encomendas_entregues.filter(Q(valor_cobrado__isnull=True) | Q(valor_cobrado=0)).count()

    # --- 3. DADOS GERAIS DO ESTOQUE ---
    pendentes = qs_todas.filter(status='PENDENTE')
    estoque_qtd = pendentes.count()
    estoque_valor_base = pendentes.aggregate(Sum('valor_base'))['valor_base__sum'] or 0
    
    # Alertas
    limite_critico = hoje - timedelta(days=120)
    limite_atencao = hoje - timedelta(days=30)
    
    alertas_criticos = pendentes.filter(data_chegada__lte=limite_critico).count()
    alertas_atencao = pendentes.filter(data_chegada__lte=limite_atencao, data_chegada__gt=limite_critico).count()
    
    clientes_incompletos = Cliente.objects.filter(Q(telefone__isnull=True) | Q(telefone='')).count()

    # --- 4. DADOS PARA O GRÁFICO ---
    grafico_labels = []
    grafico_dados = []
    
    for i in range(5, -1, -1):
        mes_ref = hoje - timedelta(days=i*30)
        inicio_mes = make_aware(datetime(mes_ref.year, mes_ref.month, 1))
        prox_mes = inicio_mes + timedelta(days=32)
        fim_mes = make_aware(datetime(prox_mes.year, prox_mes.month, 1)) - timedelta(seconds=1)
        
        soma_mes = qs_todas.filter(
            status='ENTREGUE', 
            data_entrega__range=(inicio_mes, fim_mes)
        ).aggregate(Sum('valor_cobrado'))['valor_cobrado__sum'] or 0
        
        grafico_labels.append(inicio_mes.strftime('%b/%Y'))
        grafico_dados.append(float(soma_mes))

    context = {
        'site_header': 'DROGAFOZ ENCOMENDAS',
        'title': 'Dashboard de Gestão',
        'data_inicial': data_inicial_str,
        'data_final': data_final_str,
        'ignorar_periodo': ignorar_periodo,
        'periodo_label': periodo_label,
        
        'faturamento_real': faturamento_real,
        'descontos_dados': descontos_dados,
        'ticket_medio': ticket_medio,
        'qtd_entregues': qtd_entregues, 
        'qtd_chegadas': qtd_chegadas,   
        
        'estoque_qtd': estoque_qtd,
        'estoque_valor_base': estoque_valor_base,
        'alertas_criticos': alertas_criticos,
        'alertas_atencao': alertas_atencao,
        
        'tempo_medio_dias': tempo_medio_dias,
        'top_clientes': top_clientes,
        'entregas_zeradas': entregas_zeradas,
        'clientes_incompletos': clientes_incompletos,
        
        'grafico_labels': json.dumps(grafico_labels),
        'grafico_dados': json.dumps(grafico_dados),
    }
    
    return render(request, 'admin/relatorio_ganhos.html', context)

def consulta_publica(request):
    query = request.GET.get('q')
    resultados = []
    total_geral = 0.0
    
    if query:
        # Remove caracteres especiais para comparar apenas números
        termo_limpo = query.replace('.', '').replace('-', '').strip()
        
        # Busca EXATA pelo CPF ou RG. 
        # Não usa 'icontains' para evitar matches parciais.
        # Não busca por nome para garantir privacidade.
        qs = Encomenda.objects.filter(
            Q(cliente__cpf=termo_limpo) | 
            Q(cliente__rg=termo_limpo),
            status='PENDENTE'
        ).order_by('-data_chegada')

        agora = timezone.now()

        for item in qs:
            # 1. Calcular dias em estoque
            dias_estoque = (agora - item.data_chegada).days
            if dias_estoque < 0: dias_estoque = 0
            
            # 2. Calcular multiplicador
            multiplicador = max(1, dias_estoque // 10)
            
            # 3. Calcular valor atualizado
            valor_final = float(item.valor_base) * multiplicador

            # Atributos para o template
            item.dias_display = dias_estoque
            item.valor_final_display = valor_final
            
            # Flag para destacar SOMENTE se ultrapassar 10 dias
            item.is_atrasado = (dias_estoque > 10) 
            
            resultados.append(item)
            total_geral += valor_final

    return render(request, 'publica/consulta.html', {
        'resultados': resultados, 
        'query': query,
        'total_geral': total_geral
    })

def home(request):
    return render(request, 'publica/home.html')