import os
from pathlib import Path
import dj_database_url

# Caminho base do projeto
BASE_DIR = Path(__file__).resolve().parent.parent

# --- SEGURANÇA ---
# A chave secreta será pega do servidor. Se não tiver (no seu PC), usa uma provisória.
SECRET_KEY = os.environ.get('SECRET_KEY', 'django-insecure-chave-padrao-troque-na-producao')

# DEBUG: No seu PC é True. Na nuvem (Render) será False automaticamente.
DEBUG = 'RENDER' not in os.environ

# Permite que o site seja acessado pelo seu domínio e pelo endereço do Render
ALLOWED_HOSTS = ['*']

# --- SEGURANÇA DE DOMÍNIOS (CSRF) --- <--- ÚNICA ALTERAÇÃO FEITA AQUI
# Isso permite que o login funcione no seu domínio novo e no endereço do Render
CSRF_TRUSTED_ORIGINS = [
    'https://sistema-drogafoz.onrender.com',    # Endereço original (confirme se é este mesmo)
    'https://drogafozencomendas.com.br',        # Novo domínio
    'https://www.drogafozencomendas.com.br',    # Novo domínio com www
]

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'entregas', # Seu app
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware', # Serve arquivos estáticos na nuvem
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'core.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [os.path.join(BASE_DIR, 'templates')],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'core.wsgi.application'

# --- BANCO DE DADOS (HÍBRIDO) ---
# Se estiver na nuvem (tem a variável DATABASE_URL), usa PostgreSQL (Neon).
# Se estiver no seu PC, usa o SQLite.
DATABASES = {
    'default': dj_database_url.config(
        default='sqlite:///' + os.path.join(BASE_DIR, 'db.sqlite3'),
        conn_max_age=0 # Mantido em 0 para evitar erro 500 no Neon
    )
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'pt-br'
TIME_ZONE = 'America/Sao_Paulo'
USE_I18N = True
USE_TZ = True

# --- ARQUIVOS ESTÁTICOS (CSS/JS) ---
STATIC_URL = 'static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')
# Compressão para o site carregar rápido e seguro
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'