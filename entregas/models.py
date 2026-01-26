from django.db import models
from django.core.validators import RegexValidator
from django.core.exceptions import ValidationError

class Cliente(models.Model):
    # Regra de Validação: Aceita apenas dígitos de 0 a 9
    apenas_numeros = RegexValidator(r'^\d+$', 'Este campo deve conter apenas números (sem pontos ou traços).')

    # Apenas o Nome continua obrigatório na definição do campo
    nome = models.CharField(max_length=200)
    
    # CPF: unique=True já garante que o banco não aceite CPFs iguais
    cpf = models.CharField(
        max_length=14, 
        unique=True, 
        blank=True, 
        null=True, 
        validators=[apenas_numeros],
        error_messages={'unique': 'Já existe um cliente cadastrado com este CPF.'}
    )
    
    # RG: Validaremos a unicidade manualmente no método clean()
    rg = models.CharField(
        max_length=20, 
        blank=True, 
        null=True, 
        validators=[apenas_numeros]
    )
    
    genero = models.CharField(max_length=20, choices=[('M', 'Masculino'), ('F', 'Feminino'), ('O', 'Outro')], blank=True, null=True)
    telefone = models.CharField(max_length=20, blank=True, null=True)
    email = models.EmailField(blank=True, null=True)
    
    data_cadastro = models.DateTimeField(auto_now_add=True)

    def clean(self):
        # 1. Validação de RG Único
        # (Se o RG foi preenchido, verifica se já existe outro cliente com ele)
        if self.rg:
            if Cliente.objects.filter(rg=self.rg).exclude(pk=self.pk).exists():
                raise ValidationError({'rg': 'Já existe um cliente cadastrado com este RG.'})

        # 2. Validação de Homônimos sem Documento (Regra do "Apenas Nome")
        # Se NÃO tem CPF e NÃO tem RG...
        if not self.cpf and not self.rg:
            # ...verifica se já existe alguém com esse nome exato (ignorando maiúsculas/minúsculas)
            if Cliente.objects.filter(nome__iexact=self.nome).exclude(pk=self.pk).exists():
                raise ValidationError(
                    'Já existe um cliente com este Nome no sistema. '
                    'Para cadastrar um homônimo (pessoa com mesmo nome), '
                    'é OBRIGATÓRIO informar o CPF ou o RG para diferenciá-los.'
                )

    def save(self, *args, **kwargs):
        # Garante que campos vazios sejam salvos como NULL no banco
        # Isso evita erro de "duplicidade de campo vazio"
        if not self.cpf: self.cpf = None
        if not self.rg: self.rg = None
        super().save(*args, **kwargs)

    def __str__(self):
        if self.cpf:
            return f"{self.nome} ({self.cpf})"
        return self.nome
    
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
    
    # ALTERAÇÃO AQUI: Mudado de TextField para CharField com max_length=200
    descricao = models.CharField(max_length=200, verbose_name="Descrição da Encomenda") 
    
    data_chegada = models.DateTimeField(verbose_name="Data de Chegada")
    data_entrega = models.DateTimeField(verbose_name="Data de Entrega", blank=True, null=True)
    valor_cobrado = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDENTE')

    def __str__(self):
        return f"{self.descricao} - {self.cliente.nome}"

    class Meta:
        ordering = ['cliente__nome']
        verbose_name = 'Encomenda'
        verbose_name_plural = 'Encomendas'