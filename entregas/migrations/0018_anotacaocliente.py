# entregas/migrations/0018_anotacaocliente.py
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone

class Migration(migrations.Migration):

    dependencies = [
        ('entregas', '0017_cliente_telefone2_alter_cliente_telefone'),
    ]

    operations = [
        migrations.CreateModel(
            name='AnotacaoCliente',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('anotacao', models.TextField(verbose_name='Anotação')),
                ('data_hora', models.DateTimeField(default=django.utils.timezone.now, verbose_name='Data e Hora')),
                ('cliente', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='entregas.cliente', verbose_name='Cliente')),
            ],
            options={
                'verbose_name': 'Anotação de Cliente',
                'verbose_name_plural': 'Anotações de Clientes',
            },
        ),
    ]