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
from .models import Cliente, Encomenda

admin.site.site_header = "DROGAFOZ ENCOMENDAS"
admin.site.site_title = "Drogafoz Admin"
admin.site.index_title = "Administração do Sistema"
admin.site.enable_nav_sidebar = False 

admin.site.unregister(Group)
admin.site.unregister(User)

# --- MIXIN PARA BUSCA SEM ACENTO ---
class BuscaSemAcentoMixin:
    def get_search_results(self, request, queryset, search_term):
        # Salva os campos originais
        campos_originais = self.search_fields
        # Adiciona o modificador '__unaccent' para ignorar acentos no Postgres
        self.search_fields = [f"{campo}__unaccent" for campo in self.search_fields]
        
        try:
            qs, use_distinct = super().get_search_results(request, queryset, search_term)
        finally:
            # Restaura os campos originais para não dar erro em outras partes
            self.search_fields = campos_originais
            
        return qs, use_distinct

@admin.register(User)
class CustomUserAdmin(BuscaSemAcentoMixin, UserAdmin):
    actions = None
    # Definimos explicitamente onde buscar
    search_fields = ('username', 'first_name', 'last_name', 'email')
    
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

@admin.action(description='Marcar selecionados como "Entregue ao Cliente"')
def marcar_entregue(modeladmin, request, queryset):
    if 'post' in request.POST:
        count = 0
        agora = timezone.now()
        
        for encomenda in queryset:
            input_name = f'valor_{encomenda.id}'
            novo_valor_cobrado = request.POST.get(input_name)

            if novo_valor_cobrado:
                encomenda.valor_cobrado = novo_valor_cobrado.replace(',', '.')
                encomenda.status = 'ENTREGUE'
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
        
        modeladmin.message_user(request, f"{count} encomenda(s) atualizada(s)!", messages.SUCCESS)
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
        return (('PENDENTE', 'Aguardando Retirada'), ('ENTREGUE', 'Entregue ao Cliente'), ('TODOS', 'Todas'),)

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
class ClienteAdmin(BuscaSemAcentoMixin, admin.ModelAdmin):
    actions = None
    list_display = ('get_nome_status', 'cpf', 'rg', 'genero', 'telefone', 'email')
    search_fields = ('nome', 'cpf', 'rg')
    list_per_page = 25

    @admin.display(ordering='nome', description='Nome')
    def get_nome_status(self, obj):
        if not obj.cpf or not obj.telefone:
            return format_html('<span style="color: #C51625; font-weight: bold;">{}</span>', obj.nome)
        return obj.nome

    def get_urls(self):
        urls = super().get_urls()
        my_urls = [path('exportar-xml/', self.exportar_xml),]
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
    
    list_display = ('get_cliente_nome', 'get_descricao_fmt', 'get_status_fmt', 'get_data_chegada_fmt', 'get_data_saida_fmt', 'valor_base', 'valor_calculado', 'valor_cobrado')
    
    list_filter = (StatusFilter,) 
    search_fields = ('cliente__nome',)
    autocomplete_fields = ['cliente']
    actions = [marcar_entregue]
    
    readonly_fields = ('valor_calculado',)
    fields = ('cliente', 'descricao', 'status', 'data_chegada', 'data_entrega', 'valor_base', 'valor_calculado', 'valor_cobrado')

    def _get_colored_text(self, obj, text):
        if obj.status == 'PENDENTE':
            dias = (timezone.now() - obj.data_chegada).days
            if dias > 120:
                return format_html('<span style="color: #C51625; font-weight: bold;">{}</span>', text)
        return text

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
        if not cliente.cpf or not cliente.telefone:
            return format_html('<span style="color: #C51625; font-weight: bold;">{}</span>', cliente.nome)
        return self._get_colored_text(obj, cliente.nome)

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        field = form.base_fields['cliente']
        field.widget.can_add_related = True      
        field.widget.can_change_related = True   
        field.widget.can_view_related = False    
        field.widget.can_delete_related = False  
        return form

    def get_urls(self):
        urls = super().get_urls()
        my_urls = [path('exportar-xml/', self.exportar_xml),]
        return my_urls + urls

    def exportar_xml(self, request):
        queryset = Encomenda.objects.all()
        data = serializers.serialize("xml", queryset, use_natural_foreign_keys=True)
        response = HttpResponse(data, content_type="application/xml")
        response['Content-Disposition'] = 'attachment; filename="encomendas_drogafoz.xml"'
        return response