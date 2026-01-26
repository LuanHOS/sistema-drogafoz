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
from .models import Cliente, Encomenda
import math

# --- Configurações Gerais ---
admin.site.site_header = "DROGAFOZ ENCOMENDAS"
admin.site.site_title = "Drogafoz Admin"
admin.site.index_title = "Administração do Sistema"
admin.site.enable_nav_sidebar = False 

# --- PERSONALIZAÇÃO DE USUÁRIOS ---
admin.site.unregister(Group)
admin.site.unregister(User)

@admin.register(User)
class CustomUserAdmin(UserAdmin):
    actions = None
    readonly_fields = ('date_joined', 'last_login')
    fieldsets = (
        (None, {'fields': ('username', 'password')}),
        (_('Personal info'), {'fields': ('first_name', 'last_name', 'email')}),
        (_('Permissions'), {
            'fields': ('is_active', 'is_staff', 'is_superuser'),
        }),
        (_('Important dates'), {'fields': ('last_login', 'date_joined')}),
    )

    def has_delete_permission(self, request, obj=None):
        if User.objects.count() <= 1:
            return False
        return super().has_delete_permission(request, obj)

# --- AÇÃO COM CONFIRMAÇÃO E CÁLCULO DE VALORES ---
@admin.action(description='Marcar selecionados como "Entregue ao Cliente"')
def marcar_entregue(modeladmin, request, queryset):
    if 'post' in request.POST:
        count = 0
        for encomenda in queryset:
            input_name = f'valor_{encomenda.id}'
            novo_valor = request.POST.get(input_name)

            if novo_valor:
                encomenda.valor_cobrado = novo_valor.replace(',', '.')
                encomenda.status = 'ENTREGUE'
                encomenda.data_entrega = timezone.now()
                encomenda.save()
                count += 1
        
        modeladmin.message_user(request, f"{count} encomenda(s) atualizada(s) e marcada(s) como entregue(s)!", messages.SUCCESS)
        return HttpResponseRedirect(request.get_full_path())

    tem_duplicata = queryset.filter(status='ENTREGUE').exists()
    encomendas_ordenadas = queryset.select_related('cliente').order_by('cliente__nome')
    
    resumo_agrupado = {}
    agora = timezone.now()

    for enc in encomendas_ordenadas:
        c_id = enc.cliente.id
        if c_id not in resumo_agrupado:
            resumo_agrupado[c_id] = {
                'cliente': enc.cliente,
                'itens': [],
                'total_sugerido': 0.0
            }
        
        dias_estoque = (agora - enc.data_chegada).days
        if dias_estoque < 0: dias_estoque = 0
        
        multiplicador = max(1, dias_estoque // 10)

        valor_original = float(enc.valor_cobrado)
        valor_sugerido = valor_original * multiplicador

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

# --- Filtros ---
class StatusFilter(admin.SimpleListFilter):
    title = _('Filtrar por Status')
    parameter_name = 'status'

    def lookups(self, request, model_admin):
        return (
            ('PENDENTE', 'Aguardando Retirada'),
            ('ENTREGUE', 'Entregue ao Cliente'),
            ('TODOS', 'Todas'),
        )

    def choices(self, changelist):
        total_pendente = Encomenda.objects.filter(status='PENDENTE').count()
        total_entregue = Encomenda.objects.filter(status='ENTREGUE').count()
        total_geral = Encomenda.objects.count()
        value = self.value()
        yield {'selected': value is None or value == 'PENDENTE', 'query_string': changelist.get_query_string({'status': 'PENDENTE'}, []), 'display': f'Aguardando Retirada ({total_pendente})'}
        yield {'selected': value == 'ENTREGUE', 'query_string': changelist.get_query_string({'status': 'ENTREGUE'}, []), 'display': f'Entregue ao Cliente ({total_entregue})'}
        yield {'selected': value == 'TODOS', 'query_string': changelist.get_query_string({'status': 'TODOS'}, []), 'display': f'Todas ({total_geral})'}

    def queryset(self, request, queryset):
        if self.value() == 'ENTREGUE': return queryset.filter(status='ENTREGUE')
        if self.value() == 'TODOS': return queryset
        return queryset.filter(status='PENDENTE')

@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
    # --- ALTERAÇÃO: A linha de ações foi removida ---
    actions = None
    
    list_display = ('get_nome_status', 'cpf', 'rg', 'genero', 'telefone', 'email')
    search_fields = ('nome', 'cpf', 'rg')
    list_per_page = 25

    @admin.display(ordering='nome', description='Nome')
    def get_nome_status(self, obj):
        if not obj.cpf or not obj.telefone:
            return format_html('<span style="color: #C51625; font-weight: bold;">{}</span>', obj.nome)
        return obj.nome

    # --- Exportação de XML ---
    def get_urls(self):
        urls = super().get_urls()
        my_urls = [
            path('exportar-xml/', self.exportar_xml),
        ]
        return my_urls + urls

    def exportar_xml(self, request):
        queryset = Cliente.objects.all()
        data = serializers.serialize("xml", queryset)
        response = HttpResponse(data, content_type="application/xml")
        response['Content-Disposition'] = 'attachment; filename="clientes_drogafoz.xml"'
        return response

@admin.register(Encomenda)
class EncomendaAdmin(admin.ModelAdmin):
    show_facets = admin.ShowFacets.NEVER
    list_display = ('get_cliente_nome', 'descricao', 'status', 'data_chegada', 'data_entrega')
    list_filter = (StatusFilter,) 
    search_fields = ('cliente__nome',)
    autocomplete_fields = ['cliente']
    actions = [marcar_entregue]

    @admin.display(ordering='cliente__nome', description='Cliente')
    def get_cliente_nome(self, obj):
        cliente = obj.cliente
        if not cliente.cpf or not cliente.telefone:
            return format_html('<span style="color: #C51625; font-weight: bold;">{}</span>', cliente.nome)
        return cliente.nome

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        field = form.base_fields['cliente']
        field.widget.can_add_related = True      
        field.widget.can_change_related = True   
        field.widget.can_view_related = False    
        field.widget.can_delete_related = False  
        return form

    # --- Exportação de XML ---
    def get_urls(self):
        urls = super().get_urls()
        my_urls = [
            path('exportar-xml/', self.exportar_xml),
        ]
        return my_urls + urls

    def exportar_xml(self, request):
        queryset = Encomenda.objects.all()
        data = serializers.serialize("xml", queryset, use_natural_foreign_keys=True)
        response = HttpResponse(data, content_type="application/xml")
        response['Content-Disposition'] = 'attachment; filename="encomendas_drogafoz.xml"'
        return response