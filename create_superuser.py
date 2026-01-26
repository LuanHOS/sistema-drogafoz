import os
import django
from django.contrib.auth import get_user_model

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")
django.setup()

User = get_user_model()
username = os.environ.get('DJANGO_SUPERUSER_USERNAME', 'admin')
email = os.environ.get('DJANGO_SUPERUSER_EMAIL', 'admin@example.com')
password = os.environ.get('DJANGO_SUPERUSER_PASSWORD', 'sua_senha_forte_aqui')

if not User.objects.filter(username=username).exists():
    print(f"Criando superusuário {username}...")
    User.objects.create_superuser(username, email, password)
    print("Superusuário criado com sucesso!")
else:
    print(f"Superusuário {username} já existe.")