from django.contrib import admin
from django.contrib.auth.models import Group, User
from django.contrib.auth.admin import UserAdmin
from django.utils.translation import gettext_lazy as _
from django.utils import timezone
from django.utils.html import format_html
from django.shortcuts import render
from django.http import HttpResponse, HttpResponseRedirect
from django.urls import path
from django.core import serializers
from django.contrib import messages
from django.contrib.admin.models import LogEntry, CHANGE
from django.contrib.contenttypes.models import ContentType
from django.db import connection, IntegrityError
from django.core.exceptions import ValidationError
from .models import Cliente, Encomenda
import re 

admin.site.site_header = "DROGAFOZ ENCOMENDAS"
admin.site.site_title = "Drogafoz Admin"
admin.site.index_title = "Administração do Sistema"
admin.site.enable_nav_sidebar = False 

admin.site.unregister(Group)
admin.site.unregister(User)

class BuscaSemAcentoMixin:
    def get_search_results(self, request, queryset, search_term):
        campos_originais = self.search_fields
        self.search_fields = [f"{campo}__unaccent" for campo in self.search_fields]
        try:
            qs, use_distinct = super().get_search_results(request, queryset, search_term)
        finally:
            self.search_fields = campos_originais
        return qs, use_distinct

@admin.register(User)
class CustomUserAdmin(BuscaSemAcentoMixin, UserAdmin):
    actions = None
    search_fields = ('username', 'first_name', 'last_name', 'email')
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

@admin.action(description='Marcar selecionados como "Entregue ao Cliente"')
def marcar_entregue(modeladmin, request, queryset):
    # --- CORREÇÃO DE PERSISTÊNCIA (IGNORAR FILTRO DE BUSCA) ---
    if 'post' not in request.POST:
        selected = request.POST.getlist(admin.helpers.ACTION_CHECKBOX_NAME)
        if selected:
            queryset = Encomenda.objects.filter(pk__in=selected)

    if 'post' in request.POST:
        count = 0
        agora = timezone.now()
        erros_conversao = 0
        
        for encomenda in queryset:
            input_name = f'valor_{encomenda.id}'
            valor_bruto = request.POST.get(input_name)

            if valor_bruto is not None:
                try:
                    valor_limpo = re.sub(r'[^\d.,]', '', str(valor_bruto))
                    valor_limpo = valor_limpo.replace(',', '.')
                    
                    if valor_limpo.count('.') > 1:
                         pass 

                    if valor_limpo == '':
                        valor_final = 0.00
                    else:
                        valor_final = float(valor_limpo)

                    encomenda.valor_cobrado = valor_final
                    encomenda.status = 'ENTREGUE'
                    
                    if not encomenda.data_entrega:
                         encomenda.data_entrega = agora
                    
                    encomenda.save()

                    LogEntry.objects.log_action(
                        user_id=request.user.id,
                        content_type_id=ContentType.objects.get_for_model(encomenda).pk,
                        object_id=encomenda.pk,
                        object_repr=str(encomenda),
                        action_flag=CHANGE,
                        change_message=f"Entregue via Baixa em Massa. Cobrado: {encomenda.valor_cobrado}"
                    )
                    count += 1
                
                except ValueError:
                    erros_conversao += 1
                    continue
        
        msg = f"{count} encomenda(s) atualizada(s) com sucesso!"
        if erros_conversao > 0:
            messages.warning(request, f"{msg} Atenção: {erros_conversao} valores não puderam ser entendidos e foram ignorados.")
        else:
            messages.success(request, msg)
            
        return HttpResponseRedirect(request.get_full_path())

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
        enc.valor_sugerido = valor_sugerido
        enc.multiplicador = multiplicador
        enc.alerta_prazo = multiplicador > 1

        resumo_agrupado[c_id]['itens'].append(enc)
        resumo_agrupado[c_id]['total_sugerido'] += valor_sugerido

    context = {
        'encomendas': queryset,
        'resumo_agrupado': resumo_agrupado.values(),
        'tem_duplicata': tem_duplicata,
        'title': 'Confirmação de Entrega e Pagamento',
        'opts': modeladmin.model._meta,
        'action_checkbox_name': admin.helpers.ACTION_CHECKBOX_NAME,
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

@admin.register(Cliente)
class ClienteAdmin(BuscaSemAcentoMixin, admin.ModelAdmin):
    actions = None
    list_display = ('get_nome_status', 'cpf', 'rg', 'genero', 'telefone', 'email')
    search_fields = ('nome', 'cpf', 'rg')
    list_per_page = 25
    list_max_show_all = 10000

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

@admin.register(Encomenda)
class EncomendaAdmin(BuscaSemAcentoMixin, admin.ModelAdmin):
    show_facets = admin.ShowFacets.NEVER
    
    list_display = (
        'get_cliente_nome', 'get_descricao_fmt', 'observacao', 'get_status_fmt', 
        'get_data_chegada_fmt', 'get_data_saida_fmt', 
        'get_valor_base_custom', 'get_valor_cobrado_custom'
    )
    
    # Este valor base será sobrescrito pelo método get_list_per_page abaixo
    list_per_page = 25 
    
    list_filter = (StatusFilter,) 
    search_fields = ('cliente__nome',)
    autocomplete_fields = ['cliente']
    actions = [marcar_entregue]
    
    readonly_fields = ('valor_calculado',)
    
    fieldsets = (
        ('Dados da Encomenda', {
            'fields': (
                'cliente', 
                'descricao', 
                'observacao', 
                'status', 
                'data_chegada', 
                'data_entrega', 
                'valor_base', 
                'valor_calculado', 
                'valor_cobrado'
            )
        }),
        ('Área de Controle (Zona de Perigo)', {
            'classes': ('collapse',),
            'fields': ('descartado',),
            'description': '<span style="color: red; font-weight: bold;">Cuidado:</span> Encomendas descartadas somem da lista principal.'
        }),
    )

    # --- NOVO MÉTODO: Controla itens por página dinamicamente ---
    def get_list_per_page(self, request):
        status = request.GET.get('status')
        # Se for status PENDENTE ou se não tiver filtro (padrão é pendente), mostra 500
        if status == 'PENDENTE' or status is None:
            return 500
        # Para status ENTREGUE, TODOS ou LIXEIRA, mantém 25 para não pesar
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
        if obj.status == 'ENTREGUE':
            if not obj.data_entrega:
                obj.data_entrega = timezone.now()
            if obj.data_entrega < obj.data_chegada:
                messages.error(request, "ERRO: A Data de Entrega não pode ser anterior à Data de Chegada.")
                raise ValidationError("A Data de Entrega não pode ser anterior à Data de Chegada.")

        try:
            super().save_model(request, obj, form, change)
        except IntegrityError:
            messages.error(request, "ATENÇÃO: Esta encomenda já foi cadastrada anteriormente (Duplicidade detectada). O segundo cadastro foi ignorado.")
            return

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
        field = form.base_fields['cliente']
        field.widget.can_add_related = True      
        field.widget.can_change_related = True   
        field.widget.can_view_related = False    
        field.widget.can_delete_related = False  
        if obj is None:
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