"""Django settings for config project (ERP v0.1)."""

import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Gizli bilgi koda girmez: .env dosyasından okunur (bkz. .env.example).
load_dotenv(BASE_DIR / ".env")

# Yedekleme (Asama 1 motoru + dizin) — ekran (Asama 2) bunlari kullanir.
BACKUP_DIR = BASE_DIR / "backups"
BACKUP_SCRIPT = BASE_DIR / "scripts" / "db_backup.sh"

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = os.environ.get("DJANGO_DEBUG", "False") == "True"

ALLOWED_HOSTS = [h for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",") if h]

CSRF_TRUSTED_ORIGINS = [h for h in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",") if h]


# Application definition
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'core',
    'axes',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'axes.middleware.AxesMiddleware',  # EN SON: kilit kontrolu
]

AUTHENTICATION_BACKENDS = [
    # AxesStandaloneBackend ONCE: kilitliyse girisi en basta reddeder.
    "axes.backends.AxesStandaloneBackend",
    "django.contrib.auth.backends.ModelBackend",
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'core.context.yetki',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'


# Database
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.environ["DB_NAME"],
        'USER': os.environ["DB_USER"],
        'PASSWORD': os.environ["DB_PASSWORD"],
        'HOST': os.environ.get("DB_HOST", "127.0.0.1"),
        'PORT': os.environ.get("DB_PORT", "5432"),
    }
}


# Password validation
# Kural: en az 8 karakter + 1 küçük, 1 büyük, 1 rakam, 1 sembol (Türkçe mesajlı).
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
        'OPTIONS': {'min_length': 8},
    },
    {'NAME': 'core.dogrulama.KarmaSifreDogrulayici'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
]


# Internationalization
# Tarih disiplini: UTC sakla, TR göster (USE_TZ=True). Arayüz dili TR.
LANGUAGE_CODE = 'tr'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True


# Static files
STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / "staticfiles"

# --- Kimlik doğrulama (giriş + kullanıcı bazlı ekran yetkisi; v0.1'e bilinçli eklendi) ---
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "core:pano"   # giriş sonrası PANO açılır
LOGOUT_REDIRECT_URL = "login"


# --- Güvenlik sıkılaştırma (YALNIZ prod / DEBUG=False) ---------------------
# Yerel/WSL geliştirmede (DEBUG=True) KAPALI kalır; aksi halde HTTP üzerinden
# giriş yapılamaz hale gelir (secure çerez + SSL redirect). nginx HTTPS’i
# sonlandırıyor ve X-Forwarded-Proto başlığını geçiriyor; bu header olmadan
# SECURE_SSL_REDIRECT sonsuz yönlendirme döngüsüne yol açar.
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    # HSTS: ÖNCE küçük (5 dk); canlıda çalıştığı doğrulanınca büyütülecek.
    # include_subdomains/preload ŞİMDİLİK KAPALI — yalnız test.semtahome.com’a
    # uygulanır; subdomainler ve eski sistem (erp.semtahome.com) etkilenmez.
    SECURE_HSTS_SECONDS = 300
    SECURE_HSTS_INCLUDE_SUBDOMAINS = False
    SECURE_HSTS_PRELOAD = False
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_REFERRER_POLICY = "same-origin"


# --- Login brute-force korumasi (django-axes) ------------------------------
# Yalniz prod (DEBUG=False) aktif; WSL/dev ve test (DEBUG=True) KAPALI ->
# yerel gelistirme/giris ve test suite bozulmaz.
AXES_ENABLED = not DEBUG
AXES_FAILURE_LIMIT = 5                                   # 5 hatali deneme -> kilit
AXES_COOLOFF_TIME = timedelta(minutes=30)               # 30 dk sonra otomatik acilir
AXES_LOCKOUT_PARAMETERS = [["username", "ip_address"]]  # kullanici + IP kombinasyonu
AXES_RESET_ON_SUCCESS = True                            # basarili giriste sayac sifir
AXES_LOCKOUT_TEMPLATE = "registration/kilitlendi.html"  # Turkce, sik kilit sayfasi
