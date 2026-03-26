from django.contrib import admin
from django.contrib.auth.models import Group, User
from django.contrib.auth.admin import UserAdmin
from django.utils.translation import gettext_lazy as _
from django.utils import timezone
from django.utils.html import format_html
from django.shortcuts import render, get_object_or_404
from django.http import HttpResponse, HttpResponseRedirect
from django.urls import path, reverse
from django.core import serializers
from django.contrib import messages
from django.contrib.admin.models import LogEntry, CHANGE
from django.contrib.contenttypes.models import ContentType
from django.db import connection, IntegrityError, transaction
from django.core.exceptions import ValidationError
from django import forms
from django.contrib.admin.widgets import AutocompleteSelect
from .models import Cliente, Encomenda, Retirada
import re 
import json

admin.site.site_header = "DROGAFOZ ENCOMENDAS"
admin.site.site_title = "Drogafoz Admin"
admin.site.index_title = "Administração do Sistema"
admin.site.enable_nav_sidebar = False 

admin.site.unregister(Group)
admin.site.unregister(User)

class BuscaSemAcentoMixin:
    def get_search_results(self, request, queryset, search_term):
        campos_originais = self.search_fields
        nova_busca = []
        for campo in self.search_fields:
            # Evita aplicar unaccent em campos numéricos de ID para não gerar crash no Postgres
            if campo in ['id', '=id']:
                nova_busca.append(campo)
            else:
                nova_busca.append(f"{campo}__unaccent")
        
        self.search_fields = nova_busca
        try:
            qs, use_distinct = super().get_search_results(request, queryset, search_term)
        finally:
            self.search_fields = campos_originais
        return qs, use_distinct

@admin.register(User)
class CustomUserAdmin(BuscaSemAcentoMixin, UserAdmin):
    actions = None
    search_fields = ('=id', 'username', 'first_name', 'last_name', 'email')
    readonly_fields = ('date_joined', 'last_login')
    list_per_page = 25
    list_max_show_all = 10000

    fieldsets = (
        (None, {'fields': ('username', 'password')}),
        (_('Personal info'), {'fields': ('first_name', 'last_name', 'email')}),
        (_('Permissions'), {'fields': ('is_active', 'is_staff', 'is_superuser')}),
        (_('Important dates'), {'fields': ('last_login', 'date_joined')}),
    )

    def has_delete_permission(self, request, obj=None):
        if User.objects.count() <= 1: return False
        return super().has_delete_permission(request, obj)

# --- INÍCIO CORREÇÃO 5 (FORM DO RETIRANTE) ---
class RetiranteForm(forms.Form):
    retirante = forms.ModelChoiceField(
        queryset=Cliente.objects.all().order_by('-id'),
        widget=AutocompleteSelect(Retirada._meta.get_field('retirado_por'), admin.site),
        required=True,
        label="Quem está retirando as encomendas no balcão? (Obrigatório)"
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Habilita os botões de Adicionar e Alterar no widget
        self.fields['retirante'].widget.can_add_related = True
        self.fields['retirante'].widget.can_change_related = True
        self.fields['retirante'].widget.can_view_related = False
        self.fields['retirante'].widget.can_delete_related = False
# --- FIM CORREÇÃO 5 ---

# --- NOVO: FORMULÁRIO DE ENCOMENDA COM VALIDAÇÃO SEGURA ---
class EncomendaAdminForm(forms.ModelForm):
    class Meta:
        model = Encomenda
        fields = '__all__'

    def clean(self):
        cleaned_data = super().clean()
        status = cleaned_data.get('status')
        data_chegada = cleaned_data.get('data_chegada')
        data_entrega = cleaned_data.get('data_entrega')

        if data_chegada and data_chegada > timezone.now():
            self.add_error('data_chegada', "ERRO: A Data de Chegada não pode ser uma data futura.")

        if status == 'ENTREGUE':
            if data_entrega and data_chegada and data_entrega < data_chegada:
                self.add_error('data_entrega', "ERRO: A Data de Entrega não pode ser anterior à Data de Chegada.")
        
        return cleaned_data

@admin.action(description='Marcar selecionados como "Entregue ao Cliente"')
def marcar_entregue(modeladmin, request, queryset):
    # --- GARANTE A PERSISTÊNCIA DOS IDs SUBMETIDOS EM TODAS AS ETAPAS ---
    selected = request.POST.getlist(admin.helpers.ACTION_CHECKBOX_NAME)
    if selected:
        queryset = Encomenda.objects.filter(pk__in=selected)
    elif 'post' in request.POST:
        # TRAVA 1: Impede que o Django dê baixa no queryset inteiro se a lista de IDs sumir (Lista Fantasma)
        messages.error(request, "ERRO CRÍTICO: Nenhuma encomenda válida selecionada para a baixa.")
        return HttpResponseRedirect(request.get_full_path())

    if 'post' in request.POST:
        # Puxa o campo 'retirante' gerado pelo Autocomplete
        retirante_id = request.POST.get('retirante')
        
        try:
            with transaction.atomic():
                if not retirante_id:
                    raise ValueError("Você precisa selecionar quem está retirando no balcão.")
                
                try:
                    retirante = Cliente.objects.get(pk=retirante_id)
                except Cliente.DoesNotExist:
                    raise ValueError("O cliente selecionado foi apagado ou não existe mais no sistema.")
                    
                agora = timezone.now()
                
                retirada = Retirada.objects.create(
                    retirado_por=retirante,
                    operador=request.user,
                    valor_total=0,
                    data_retirada=agora
                )
                # Força atualizar a data caso o auto_now_add bugue a transação atômica
                Retirada.objects.filter(pk=retirada.pk).update(data_retirada=agora)
                
                count = 0
                erros_conversao = 0
                total_cobrado = 0.0
                
                # TRAVA 2: Bloqueio de Concorrência Real no Banco de Dados
                encomendas_lock = Encomenda.objects.select_for_update().filter(pk__in=selected)
                
                # NOVA TRAVA: Verificação contra exclusão de pacotes durante a operação
                if len(encomendas_lock) != len(selected):
                    raise ValueError("Algumas encomendas selecionadas foram apagadas do sistema por outro utilizador. Operação abortada por segurança.")
                
                for encomenda in encomendas_lock:
                    if encomenda.status == 'ENTREGUE':
                        raise ValueError(f"A encomenda #{encomenda.id} já foi entregue em outro caixa. Operação abortada para evitar faturamento duplicado.")

                    input_name = f'valor_{encomenda.id}'
                    valor_bruto = request.POST.get(input_name)

                    if valor_bruto is None:
                        raise ValueError(f"Falta o valor final para a encomenda #{encomenda.id}. Abortando baixa de segurança.")

                    try:
                        valor_limpo = re.sub(r'[^\d.,]', '', str(valor_bruto))
                        valor_limpo = valor_limpo.replace(',', '.')
                        
                        # TRAVA 3: Proteção contra injeção de múltiplos pontos (Erro 500)
                        if valor_limpo.count('.') > 1:
                            partes = valor_limpo.split('.')
                            valor_limpo = ''.join(partes[:-1]) + '.' + partes[-1]

                        if valor_limpo == '':
                            valor_final = 0.00
                        else:
                            valor_final = float(valor_limpo)

                        encomenda.valor_cobrado = valor_final
                        encomenda.status = 'ENTREGUE'
                        encomenda.retirada = retirada
                        
                        if not encomenda.data_entrega:
                             encomenda.data_entrega = agora
                        
                        encomenda.save()
                        total_cobrado += valor_final

                        LogEntry.objects.log_action(
                            user_id=request.user.id,
                            content_type_id=ContentType.objects.get_for_model(encomenda).pk,
                            object_id=encomenda.pk,
                            object_repr=str(encomenda),
                            action_flag=CHANGE,
                            change_message=f"Baixado na Retirada #{retirada.id}. Cobrado: {encomenda.valor_cobrado}"
                        )
                        count += 1
                    
                    except ValueError as e:
                        erros_conversao += 1
                        raise ValueError(f"Erro de conversão financeira no pacote #{encomenda.id}: {str(e)}")
                    except Exception as e:
                        raise Exception(f"Erro ao salvar pacote #{encomenda.id}: {str(e)}")
                
                retirada.valor_total = total_cobrado
                retirada.save()

                msg = f"{count} encomenda(s) baixadas com sucesso! Retirada #{retirada.id} registrada."
                if erros_conversao > 0:
                    messages.warning(request, f"{msg} Atenção: {erros_conversao} valores ignorados.")
                else:
                    messages.success(request, msg)

                # NOVO REDIRECIONAMENTO: Vai direto para a visualização da retirada gerada
                return HttpResponseRedirect(reverse('admin:entregas_retirada_change', args=[retirada.pk]))

        except Exception as e:
            # Em vez de redirecionar e apagar a tela, disparamos o erro e deixamos re-renderizar
            messages.error(request, f"Ação Revertida de forma atómica (Rollback executado). Corrija e tente novamente. Detalhes: {str(e)}")
            # TRAVA 4: Limpa o cache de objetos corrompidos na memória Python após o Rollback do banco
            queryset = Encomenda.objects.filter(pk__in=selected)

    tem_duplicata = queryset.filter(status='ENTREGUE').exists()
    encomendas_ordenadas = queryset.select_related('cliente').order_by('cliente__nome')
    
    resumo_agrupado = {}
    agora = timezone.now()

    for enc in encomendas_ordenadas:
        c_id = enc.cliente.id
        if c_id not in resumo_agrupado:
            resumo_agrupado[c_id] = {'cliente': enc.cliente, 'itens': [], 'total_sugerido': 0.0}
        
        dias_estoque = (agora - enc.data_chegada).days
        if dias_estoque < 0: dias_estoque = 0
        multiplicador = max(1, dias_estoque // 10)

        valor_base_float = float(enc.valor_base)
        valor_sugerido = valor_base_float * multiplicador

        enc.dias_estoque = dias_estoque
        enc.multiplicador = multiplicador
        enc.alerta_prazo = multiplicador > 1

        # Lógica de persistência em caso de rollback: Recupera os dados POSTados
        if 'post' in request.POST and f'valor_{enc.id}' in request.POST:
            try:
                val_str = request.POST.get(f'valor_{enc.id}')
                val_limpo = re.sub(r'[^\d.,]', '', str(val_str)).replace(',', '.')
                enc.valor_sugerido = float(val_limpo) if val_limpo else 0.0
            except ValueError:
                enc.valor_sugerido = valor_sugerido
        else:
            enc.valor_sugerido = valor_sugerido

        resumo_agrupado[c_id]['itens'].append(enc)
        resumo_agrupado[c_id]['total_sugerido'] += enc.valor_sugerido

    # --- LÓGICA DE VERIFICAÇÃO DE ENCOMENDAS ESQUECIDAS E EXTRAS ---
    clientes_ids = list(resumo_agrupado.keys())
    selecionados_ids = list(queryset.values_list('id', flat=True))
    
    encomendas_esquecidas_qs = Encomenda.objects.filter(
        cliente_id__in=clientes_ids,
        status='PENDENTE',
        descartado=False
    ).exclude(id__in=selecionados_ids).select_related('cliente').order_by('cliente__nome', 'data_chegada')

    esquecidas_agrupadas = {}
    todas_esquecidas_ids = []

    for enc in encomendas_esquecidas_qs:
        c_nome = enc.cliente.nome
        if c_nome not in esquecidas_agrupadas:
            esquecidas_agrupadas[c_nome] = []
        esquecidas_agrupadas[c_nome].append({
            'id': enc.id,
            'descricao': enc.descricao,
            'remetente': enc.remetente,
            'observacao': enc.observacao
        })
        todas_esquecidas_ids.append(enc.pk)

    # Força a ordenação das encomendas extras pelo ID (-id)
    todas_pendentes = Encomenda.objects.filter(
        status='PENDENTE', 
        descartado=False
    ).exclude(id__in=selecionados_ids).select_related('cliente').order_by('-id')

    # --- DICIONÁRIO COMPLETO DE CLIENTES PARA O JS ---
    clientes_dados = {
        str(c.id): {
            'nome': c.nome,
            'cpf': c.cpf or '',
            'rg': c.rg or '',
            'telefone': c.telefone or '',
            'email': c.email or ''
        } for c in Cliente.objects.all()
    }

    # Mantém o form preenchido caso tenha ocorrido falha no atomic rollback
    retirante_form = RetiranteForm(request.POST if 'post' in request.POST else None)

    context = {
        'encomendas': queryset,
        'resumo_agrupado': resumo_agrupado.values(),
        'tem_duplicata': tem_duplicata,
        'title': 'Confirmação de Entrega e Pagamento',
        'opts': modeladmin.model._meta,
        'action_checkbox_name': admin.helpers.ACTION_CHECKBOX_NAME,
        'esquecidas_agrupadas': esquecidas_agrupadas,
        'todas_esquecidas_ids': todas_esquecidas_ids,
        'todas_pendentes': todas_pendentes,
        'retirante_form': retirante_form,
        'clientes_dados_json': json.dumps(clientes_dados),
    }
    return render(request, 'admin/confirmar_entrega.html', context)

class StatusFilter(admin.SimpleListFilter):
    title = _('Filtrar por Status')
    parameter_name = 'status'

    def lookups(self, request, model_admin):
        return (
            ('PENDENTE', 'Aguardando Retirada'), 
            ('ENTREGUE', 'Entregue ao Cliente'), 
            ('TODOS', 'Todas (Ativas)'),
            ('LIXEIRA', 'Lixeira / Descartados')
        )

    def choices(self, changelist):
        total_pendente = Encomenda.objects.filter(status='PENDENTE', descartado=False).count()
        total_entregue = Encomenda.objects.filter(status='ENTREGUE', descartado=False).count()
        total_geral = Encomenda.objects.filter(descartado=False).count()
        total_lixeira = Encomenda.objects.filter(descartado=True).count()
        
        value = self.value()
        
        yield {
            'selected': value is None or value == 'PENDENTE', 
            'query_string': changelist.get_query_string({'status': 'PENDENTE'}, []), 
            'display': f'Aguardando Retirada ({total_pendente})'
        }
        yield {
            'selected': value == 'ENTREGUE', 
            'query_string': changelist.get_query_string({'status': 'ENTREGUE'}, []), 
            'display': f'Entregue ao Cliente ({total_entregue})'
        }
        yield {
            'selected': value == 'TODOS', 
            'query_string': changelist.get_query_string({'status': 'TODOS'}, []), 
            'display': f'Todas ({total_geral})'
        }
        yield {
            'selected': value == 'LIXEIRA', 
            'query_string': changelist.get_query_string({'status': 'LIXEIRA'}, []), 
            'display': f'Itens Descartados ({total_lixeira})'
        }

    def queryset(self, request, queryset):
        if self.value() == 'LIXEIRA':
            return queryset.filter(descartado=True)
        if self.value() == 'ENTREGUE': 
            return queryset.filter(status='ENTREGUE', descartado=False)
        if self.value() == 'TODOS': 
            return queryset.filter(descartado=False)
        return queryset.filter(status='PENDENTE', descartado=False)

class RetiradaStatusFilter(admin.SimpleListFilter):
    title = _('Filtrar por Status')
    parameter_name = 'status'

    def lookups(self, request, model_admin):
        return (
            ('ATIVA', 'Ativas'), 
            ('CANCELADA', 'Canceladas'), 
            ('TODAS', 'Todas'),
        )

    def choices(self, changelist):
        total_ativas = Retirada.objects.filter(status='ATIVA').count()
        total_canceladas = Retirada.objects.filter(status='CANCELADA').count()
        total_geral = Retirada.objects.count()
        
        value = self.value()
        
        yield {
            'selected': value is None or value == 'ATIVA', 
            'query_string': changelist.get_query_string({'status': 'ATIVA'}, []), 
            'display': f'Ativas ({total_ativas})'
        }
        yield {
            'selected': value == 'CANCELADA', 
            'query_string': changelist.get_query_string({'status': 'CANCELADA'}, []), 
            'display': f'Canceladas ({total_canceladas})'
        }
        yield {
            'selected': value == 'TODAS', 
            'query_string': changelist.get_query_string({'status': 'TODAS'}, []), 
            'display': f'Todas ({total_geral})'
        }

    def queryset(self, request, queryset):
        if self.value() == 'CANCELADA':
            return queryset.filter(status='CANCELADA')
        if self.value() == 'TODAS': 
            return queryset
        return queryset.filter(status='ATIVA')

@admin.register(Retirada)
class RetiradaAdmin(admin.ModelAdmin):
    list_display = ('id', 'get_retirado_por_nome', 'get_qtd_clientes', 'get_qtd_encomendas', 'get_data_hora', 'get_valor_total_fmt')
    list_filter = (RetiradaStatusFilter, 'data_retirada', 'operador')
    search_fields = ('=id', 'retirado_por__nome', 'retirado_por__cpf')
    
    @admin.display(description='Retirado Por', ordering='retirado_por__nome')
    def get_retirado_por_nome(self, obj):
        return obj.retirado_por.nome

    @admin.display(description='Qtd de Clientes')
    def get_qtd_clientes(self, obj):
        return obj.encomendas.values('cliente').distinct().count()

    @admin.display(description='Qtd de Encomendas')
    def get_qtd_encomendas(self, obj):
        return obj.encomendas.count()

    @admin.display(description='Data e Hora da Retirada', ordering='data_retirada')
    def get_data_hora(self, obj):
        if obj.data_retirada:
            return timezone.localtime(obj.data_retirada).strftime('%d/%m/%Y %H:%M')
        return "-"

    @admin.display(description='Valor Total Cobrado', ordering='valor_total')
    def get_valor_total_fmt(self, obj):
        if obj.valor_total is not None:
            return f"R$ {obj.valor_total:.2f}".replace('.', ',')
        return "R$ 0,00"
    
    def has_add_permission(self, request):
        return False
    
    def has_delete_permission(self, request, obj=None):
        return False
    
    def has_change_permission(self, request, obj=None):
        return False 
        
    def get_urls(self):
        urls = super().get_urls()
        my_urls = [
            path('<int:object_id>/cancelar/', self.admin_site.admin_view(self.cancelar_retirada), name='entregas_retirada_cancelar'),
            path('exportar-xml/', self.exportar_xml),
        ]
        return my_urls + urls

    def exportar_xml(self, request):
        queryset = Retirada.objects.all()
        data = serializers.serialize("xml", queryset)
        response = HttpResponse(data, content_type="application/xml")
        response['Content-Disposition'] = 'attachment; filename="retiradas_drogafoz.xml"'
        return response

    def cancelar_retirada(self, request, object_id):
        retirada = get_object_or_404(Retirada, pk=object_id)
        if retirada.status == 'ATIVA':
            try:
                with transaction.atomic():
                    # TRAVA DE CONCORRÊNCIA NO ROLLBACK: Garante segurança se dois caixas cancelarem ao mesmo tempo
                    retirada_lock = Retirada.objects.select_for_update().get(pk=retirada.pk)
                    if retirada_lock.status == 'ATIVA':
                        retirada_lock.status = 'CANCELADA'
                        retirada_lock.save()
                        
                        for enc in list(retirada_lock.encomendas.all()):
                            enc.status = 'PENDENTE'
                            enc.retirada = None
                            enc.data_entrega = None
                            enc.valor_cobrado = None
                            enc.save() 
                            
                        messages.success(request, f"Retirada #{retirada_lock.id} cancelada com sucesso. As encomendas voltaram ao stock e o recibo foi limpo.")
                        
                        LogEntry.objects.log_action(
                            user_id=request.user.id, 
                            content_type_id=ContentType.objects.get_for_model(retirada_lock).pk,
                            object_id=retirada_lock.pk, 
                            object_repr=str(retirada_lock), 
                            action_flag=CHANGE, 
                            change_message=f"Rollback manual efetuado"
                        )
            except Exception as e:
                messages.error(request, f"Ação Revertida de forma atómica. Erro ao cancelar retirada: {e}")
        return HttpResponseRedirect(reverse('admin:entregas_retirada_changelist'))

    def change_view(self, request, object_id, form_url='', extra_context=None):
        retirada = get_object_or_404(Retirada, pk=object_id)
        encomendas = retirada.encomendas.all().select_related('cliente')
        
        resumo_agrupado = {}
        for enc in encomendas:
            c_id = enc.cliente.id
            if c_id not in resumo_agrupado:
                resumo_agrupado[c_id] = {'cliente': enc.cliente, 'itens': [], 'subtotal': 0.0}
            
            dias_estoque = (enc.data_entrega - enc.data_chegada).days if enc.data_entrega else 0
            if dias_estoque < 0: dias_estoque = 0
            enc.dias_estoque_calculado = dias_estoque
            
            resumo_agrupado[c_id]['itens'].append(enc)
            if enc.valor_cobrado:
                resumo_agrupado[c_id]['subtotal'] += float(enc.valor_cobrado)
                
        extra_context = extra_context or {}
        extra_context['retirada'] = retirada
        extra_context['resumo_agrupado'] = resumo_agrupado.values()
        extra_context['show_save'] = False
        extra_context['show_save_and_continue'] = False
        extra_context['show_delete'] = False
        
        return render(request, 'admin/visualizar_retirada.html', extra_context)

@admin.register(Cliente)
class ClienteAdmin(BuscaSemAcentoMixin, admin.ModelAdmin):
    actions = None
    list_display = ('id', 'get_nome_status', 'cpf', 'rg', 'genero', 'telefone', 'email')
    search_fields = ('=id', 'nome', 'cpf', 'rg')
    list_per_page = 25
    list_max_show_all = 10000
    readonly_fields = ('id',)
    fields = ('id', 'nome', 'cpf', 'rg', 'genero', 'telefone', 'email')

    # --- A SOLUÇÃO: ORDENAÇÃO EXCLUSIVA PARA O AUTOCOMPLETE ---
    def get_ordering(self, request):
        # Se a busca estiver vindo da caixinha dinâmica do Autocomplete, forçamos o ID invertido
        if request.resolver_match and request.resolver_match.url_name == 'autocomplete':
            return ['-id']
        # Caso contrário (telas e listas normais), mantém a ordem definida no model (Alfabética)
        return super().get_ordering(request)

    @admin.display(ordering='nome', description='Nome')
    def get_nome_status(self, obj):
        tem_documento = obj.cpf or obj.rg
        tem_contato = obj.telefone or obj.email
        if not tem_documento or not tem_contato:
            return format_html('<span style="color: #C51625; font-weight: bold;">{}</span>', obj.nome)
        return obj.nome

    def get_urls(self):
        urls = super().get_urls()
        my_urls = [path('exportar-xml/', self.exportar_xml)]
        return my_urls + urls

    def exportar_xml(self, request):
        queryset = Cliente.objects.all()
        data = serializers.serialize("xml", queryset)
        response = HttpResponse(data, content_type="application/xml")
        response['Content-Disposition'] = 'attachment; filename="clientes_drogafoz.xml"'
        return response

    def has_delete_permission(self, request, obj=None):
        if obj:
            # Trava de segurança: impede exclusão em cascata se o cliente tiver histórico
            tem_encomendas = Encomenda.objects.filter(cliente=obj).exists()
            tem_retiradas = Retirada.objects.filter(retirado_por=obj).exists()
            if tem_encomendas or tem_retiradas:
                return False
        return super().has_delete_permission(request, obj)

    def save_model(self, request, obj, form, change):
        # REMOVIDO try/except perigoso. A validação nativa do Django é mais segura.
        super().save_model(request, obj, form, change)

@admin.register(Encomenda)
class EncomendaAdmin(BuscaSemAcentoMixin, admin.ModelAdmin):
    form = EncomendaAdminForm
    show_facets = admin.ShowFacets.NEVER
    
    list_display = (
        'id', 'get_cliente_nome', 'get_descricao_fmt', 'get_remetente_fmt', 'observacao', 'get_status_fmt', 
        'get_data_chegada_fmt', 'get_data_saida_fmt', 
        'get_valor_base_custom', 'get_valor_cobrado_custom'
    )
    
    list_filter = (StatusFilter,) 
    search_fields = ('=id', 'cliente__nome')
    autocomplete_fields = ['cliente']
    actions = [marcar_entregue]
    
    def get_readonly_fields(self, request, obj=None):
        if obj and hasattr(obj, 'retirada_id') and obj.retirada_id:
            return ('id', 'cliente', 'descricao', 'remetente', 'observacao', 'status', 'data_chegada', 'data_entrega', 'valor_base', 'valor_calculado', 'valor_cobrado', 'descartado', 'retirada')
        return ('id', 'valor_calculado', 'status', 'retirada', 'data_entrega', 'valor_cobrado')

    def has_delete_permission(self, request, obj=None):
        if obj and hasattr(obj, 'retirada_id') and obj.retirada_id:
            return False
        return super().has_delete_permission(request, obj)
    
    fieldsets = (
        ('Dados da Encomenda', {
            'fields': (
                'id',
                'cliente', 
                'descricao', 
                'remetente',
                'observacao', 
                'status', 
                'data_chegada', 
                'data_entrega', 
                'valor_base', 
                'valor_calculado', 
                'valor_cobrado',
                'retirada'
            )
        }),
        ('Área de Controle (Zona de Perigo)', {
            'classes': ('collapse',),
            'fields': ('descartado',),
            'description': '<span style="color: red; font-weight: bold;">Cuidado:</span> Encomendas descartadas somem da lista principal.'
        }),
    )

    def get_list_per_page(self, request):
        status = request.GET.get('status')
        if status == 'PENDENTE' or status is None:
            return 10000 
        return 25

    def get_changelist(self, request, **kwargs):
        from django.contrib.admin.views.main import ChangeList
        class EncomendaChangeList(ChangeList):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                status = request.GET.get('status')
                
                if status == 'PENDENTE' or status is None:
                    self.list_max_show_all = 10000
                else:
                    self.list_max_show_all = 200
        return EncomendaChangeList

    def _get_colored_text(self, obj, text):
        if obj.status == 'PENDENTE':
            dias = (timezone.now() - obj.data_chegada).days
            if dias > 120:
                return format_html('<span style="color: #C51625; font-weight: bold;">{}</span>', text)
        return text

    def save_model(self, request, obj, form, change):
        if obj.status == 'ENTREGUE' and not obj.data_entrega:
            obj.data_entrega = timezone.now()
        # REMOVIDO try/except com "pass" que causava falhas silenciosas no ecrã.
        super().save_model(request, obj, form, change)

    def response_add(self, request, obj, post_url_continue=None):
        if not request.GET.get('_popup') and not request.POST.get('_popup'):
            from django.urls import reverse
            url = reverse('admin:entregas_encomenda_add')
            return HttpResponseRedirect(f"{url}?saved_id={obj.pk}&saved_client_id={obj.cliente.pk}")
        return super().response_add(request, obj, post_url_continue)

    @admin.display(ordering='valor_base', description='Valor Base')
    def get_valor_base_custom(self, obj):
        return obj.valor_base

    @admin.display(ordering='valor_calculado', description='Valor Calculado')
    def get_valor_calculado_custom(self, obj):
        return obj.valor_calculado

    @admin.display(ordering='valor_cobrado', description='Valor Final')
    def get_valor_cobrado_custom(self, obj):
        return obj.valor_cobrado

    @admin.display(ordering='descricao', description='Descrição')
    def get_descricao_fmt(self, obj):
        return self._get_colored_text(obj, obj.descricao)

    @admin.display(ordering='remetente', description='Remetente')
    def get_remetente_fmt(self, obj):
        return self._get_colored_text(obj, obj.remetente)

    @admin.display(ordering='status', description='Status')
    def get_status_fmt(self, obj):
        return self._get_colored_text(obj, obj.get_status_display())

    @admin.display(description='Data Chegada', ordering='data_chegada')
    def get_data_chegada_fmt(self, obj):
        valor = obj.data_chegada.strftime('%d/%m/%Y')
        return self._get_colored_text(obj, valor)

    @admin.display(description='Data Saída', ordering='data_entrega')
    def get_data_saida_fmt(self, obj):
        if obj.data_entrega:
            return obj.data_entrega.strftime('%d/%m/%Y')
        return "-"

    @admin.display(ordering='cliente__nome', description='Cliente')
    def get_cliente_nome(self, obj):
        cliente = obj.cliente
        tem_documento = cliente.cpf or cliente.rg
        tem_contato = cliente.telefone or cliente.email
        if not tem_documento or not tem_contato:
            return format_html('<span style="color: #C51625; font-weight: bold;">{}</span>', cliente.nome)
        return self._get_colored_text(obj, cliente.nome)

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        
        # PREVENÇÃO ERRO 500: Verifica se o campo 'cliente' existe antes de interagir com o widget
        if 'cliente' in form.base_fields:
            field = form.base_fields['cliente']
            field.widget.can_add_related = True      
            field.widget.can_change_related = True   
            field.widget.can_view_related = False    
            field.widget.can_delete_related = False  
            
        if obj is None and 'data_chegada' in form.base_fields:
            form.base_fields['data_chegada'].initial = timezone.now()
            
        return form

    def get_urls(self):
        urls = super().get_urls()
        my_urls = [path('exportar-xml/', self.exportar_xml)]
        return my_urls + urls

    def exportar_xml(self, request):
        queryset = Encomenda.objects.all()
        data = serializers.serialize("xml", queryset, use_natural_foreign_keys=True)
        response = HttpResponse(data, content_type="application/xml")
        response['Content-Disposition'] = 'attachment; filename="encomendas_drogafoz.xml"'
        return response