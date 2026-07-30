from django import template
from django.db.models import Sum
from django.utils import timezone
from entregas.models import Encomenda, PalavraChave, AnotacaoCliente, Cliente

register = template.Library()

@register.simple_tag
def get_stats():
    now = timezone.now()
    
    # 1. Calcula lucro do mês atual (apenas das entregues neste mês/ano)
    lucro = Encomenda.objects.filter(
        status='ENTREGUE',
        data_entrega__month=now.month,
        data_entrega__year=now.year
    ).aggregate(Sum('valor_cobrado'))['valor_cobrado__sum'] or 0
    
    # 2. Conta quantas estão no armazém (Pendentes e Não Descartadas)
    estoque = Encomenda.objects.filter(status='PENDENTE', descartado=False).count()
    
    # Busca as palavras-chave para o Post-it
    palavras = PalavraChave.objects.all().order_by('-id')

    # Busca as anotações e os clientes para o modal de anotações da tela inicial
    anotacoes = AnotacaoCliente.objects.select_related('cliente').order_by('-data_hora')
    clientes_lista = Cliente.objects.all().order_by('nome')
    
    return {
        'lucro': lucro,
        'estoque': estoque,
        'palavras': palavras,
        'anotacoes': anotacoes,
        'clientes_lista': clientes_lista,
    }