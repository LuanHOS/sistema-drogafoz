# Generated manually by AI to support remote deployment
from django.db import migrations, models

class Migration(migrations.Migration):

    dependencies = [
        ('entregas', '0008_ativar_unaccent'),
    ]

    operations = [
        migrations.AddField(
            model_name='encomenda',
            name='descartado',
            field=models.BooleanField(default=False, verbose_name='Descartar Encomenda'),
        ),
        migrations.AddField(
            model_name='encomenda',
            name='observacao',
            field=models.CharField(blank=True, max_length=150, null=True, verbose_name='Observação'),
        ),
    ]