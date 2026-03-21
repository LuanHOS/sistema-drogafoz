# Generated manually
from django.db import migrations

def corrigir_datas_retiradas(apps, schema_editor):
    Retirada = apps.get_model('entregas', 'Retirada')
    
    # Varre todas as retiradas existentes
    for retirada in Retirada.objects.all():
        primeira_encomenda = retirada.encomendas.first()
        # Se ela tiver uma encomenda, pega a data de entrega verdadeira e injeta na Retirada
        if primeira_encomenda and primeira_encomenda.data_entrega:
            Retirada.objects.filter(pk=retirada.pk).update(data_retirada=primeira_encomenda.data_entrega)

class Migration(migrations.Migration):

    dependencies = [
        ('entregas', '0013_retirada'),
    ]

    operations = [
        migrations.RunPython(corrigir_datas_retiradas),
    ]