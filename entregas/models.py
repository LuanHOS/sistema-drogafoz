from django.db import models
from django.core.validators import RegexValidator
from django.core.exceptions import ValidationError

# --- VALIDADOR DE CPF ---
def validar_cpf_algoritmo(value):
    value = str(value)
    if not value.isdigit():
        raise ValidationError('O CPF deve conter apenas números.')
    
    if len(value) != 11:
        raise ValidationError('O CPF deve ter 11 dígitos.')
    
    if value == value[0] * len(value):
        raise ValidationError('CPF inválido.')

    for i in range(9, 11):
        val = sum((int(value[num]) * ((i + 1) - num) for num in range(0, i)))
        digit = ((val * 10) % 11) % 10
        if digit != int(value[i]):
            raise ValidationError('CPF inválido (Dígitos verificadores não conferem).')

class Cliente(models.Model):
    apenas_numeros = RegexValidator(r'^\d+$', 'Este campo deve conter apenas números (sem pontos ou traços).')

    nome = models.CharField(max_length=200)
    
    # CPF já possui unique=True, garantindo que não se repita entre CPFs
    cpf = models.CharField(
        max_length=14, unique=True, blank=True, null=True, 
        validators=[apenas_numeros, validar_cpf_algoritmo],
        error_messages={'unique': 'Já existe um cliente cadastrado com este CPF.'}
    )
    
    rg = models.CharField(max_length=20, blank=True, null=True, validators=[apenas_numeros])
    
    genero = models.CharField(max_length=20, choices=[('M', 'Masculino'), ('F', 'Feminino'), ('O', 'Outro')], blank=True, null=True)
    telefone = models.CharField(max_length=20, blank=True, null=True)
    email = models.EmailField(blank=True, null=True)
    data_cadastro = models.DateTimeField(auto_now_add=True)

    def clean(self):
        # 1. Validação de Unicidade de RG (RG não pode ser igual a outro RG)
        if self.rg:
            if Cliente.objects.filter(rg=self.rg).exclude(pk=self.pk).exists():
                raise ValidationError({'rg': 'Já existe um cliente cadastrado com este RG.'})

            # 2. NOVA VALIDAÇÃO (SOLICITADA): RG não pode ser igual a um CPF existente
            # Isso impede que o RG digitado coincida com o CPF de qualquer pessoa no sistema
            if Cliente.objects.filter(cpf=self.rg).exists():
                raise ValidationError({'rg': 'Este número já está cadastrado como CPF no sistema. Por segurança, ele não pode ser usado como RG.'})

        # 3. Validação de Nome (se não tiver docs)
        if not self.cpf and not self.rg:
            if Cliente.objects.filter(nome__iexact=self.nome).exclude(pk=self.pk).exists():
                raise ValidationError('Já existe um cliente com este Nome. Informe CPF ou RG.')

    def save(self, *args, **kwargs):
        if not self.cpf: self.cpf = None
        if not self.rg: self.rg = None
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.nome} ({self.cpf})" if self.cpf else self.nome
    
    class Meta:
        ordering = ['nome']
        verbose_name = 'Cliente'
        verbose_name_plural = 'Clientes'

class Encomenda(models.Model):
    STATUS_CHOICES = [
        ('PENDENTE', 'Aguardando Retirada'),
        ('ENTREGUE', 'Entregue ao Cliente'),
    ]

    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE)
    descricao = models.CharField(max_length=200, verbose_name="Descrição da Encomenda")
    
    # CAMPO NOVO: Observação
    observacao = models.CharField(max_length=150, blank=True, null=True, verbose_name="Observação")
    
    data_chegada = models.DateTimeField(verbose_name="Data de Chegada")
    data_entrega = models.DateTimeField(verbose_name="Data de Entrega", blank=True, null=True)
    
    valor_base = models.DecimalField(max_digits=10, decimal_places=2, default=10.00, verbose_name="Valor Base")
    
    valor_calculado = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True, verbose_name="Valor Calculado")
    
    valor_cobrado = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True, verbose_name="Valor Final Cobrado")
    
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDENTE')

    # CAMPO NOVO: Switch de Descarte
    descartado = models.BooleanField(default=False, verbose_name="Descartar Encomenda")

    def save(self, *args, **kwargs):
        # Se voltar para Pendente, limpa TUDO (incluindo o cobrado)
        if self.status == 'PENDENTE':
            self.data_entrega = None
            self.valor_calculado = None
            self.valor_cobrado = None 
        
        elif self.data_entrega and self.valor_base:
            dias_estoque = (self.data_entrega - self.data_chegada).days
            if dias_estoque < 0: dias_estoque = 0
            multiplicador = max(1, dias_estoque // 10)
            self.valor_calculado = self.valor_base * multiplicador

        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.descricao} - {self.cliente.nome}"

    class Meta:
        ordering = ['cliente__nome']
        verbose_name = 'Encomenda'
        verbose_name_plural = 'Encomendas'