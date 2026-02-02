from django.db import migrations

class Migration(migrations.Migration):

    dependencies = [
        ('entregas', '0007_alter_encomenda_valor_cobrado'),
    ]

    operations = [
        # Ativa a extensão 'unaccent' no PostgreSQL
        migrations.RunSQL("CREATE EXTENSION IF NOT EXISTS unaccent;"),
    ]