from django import template
from django.db.models import Sum
from django.utils import timezone
from entregas.models import Encomenda

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
    
    # 2. Conta quantas estão no armazém (Pendentes)
    estoque = Encomenda.objects.filter(status='PENDENTE').count()
    
    return {
        'lucro': lucro,
        'estoque': estoque,
    }