import os
from pathlib import Path

from dotenv import load_dotenv

# 加载.env
# ==================================================================
load_dotenv()

# 根目录
# ==================================================================
BASE_DIR = Path(__file__).resolve().parent.parent

# 项目密钥
# ==================================================================
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY")

# DEBUG开关
# ==================================================================
DEBUG = os.environ.get("DJANGO_DEBUG", "False").lower() == "true"

# sitemap专用域名
# ==================================================================
SITE_DOMAIN = os.environ.get("DJANGO_DOMAIN", "passport.sizuya.net")

# 自动补齐末尾/
# ==================================================================
APPEND_SLASH = True

# iframe 防点击劫持
# ==================================================================
X_FRAME_OPTIONS = "DENY"

# HSTS
# ==================================================================
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

# Session安全
# ==================================================================
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True

# Cookies安全
# ==================================================================
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# 强制Content-Type
# ==================================================================
SECURE_CONTENT_TYPE_NOSNIFF = True

# XSS 过滤器
# ==================================================================
SECURE_BROWSER_XSS_FILTER = True

# URL入口
# ==================================================================
ROOT_URLCONF = "core.urls"

# 服务器入口
# ==================================================================
WSGI_APPLICATION = "core.wsgi.application"

# 主键 id 默认用“64 位自增整数
# ==================================================================
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# 日志系统
# ==================================================================
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
}

# 语言配置（官方标准）
# ==================================================================
USE_I18N = True
LANGUAGE_CODE = "en"
LOCALE_PATHS = [
    BASE_DIR / "locale",
]
PREFIX_DEFAULT_LANGUAGE = True
LANGUAGES = [
    ("zh-hans", "简体中文"),
    ("zh-hant", "正體中文"),
    ("en", "English"),
    ("ja", "日本語"),
    ("ko", "한국어"),
    ("es", "Español"),
    ("fr", "Français"),
    ("de", "Deutsch"),
    ("ar", "العربية"),
]

# 时区 / 本地化
# ==================================================================
TIME_ZONE = "Asia/Tokyo"
USE_TZ = True

# HOSTS校验
# ==================================================================
ALLOWED_HOSTS = [
    SITE_DOMAIN,
    "passport.sizuya.net",
    "127.0.0.1",
    "localhost",
]

# CSRF校验
# ==================================================================
CSRF_TRUSTED_ORIGINS = [
    f"https://{SITE_DOMAIN}",
    "https://passport.sizuya.net",
    "http://127.0.0.1",
    "http://localhost",
]

# 安装的应用
# ==================================================================
INSTALLED_APPS = [
    "core.apps.CoreConfig",
    "core.apps.CustomAuthConfig",
    "core.apps.CustomAdminConfig",
    "passport.apps.PassportConfig",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.staticfiles",
    "django.contrib.messages",
]

# 中间件
# ==================================================================
MIDDLEWARE = [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

# 模板配置
# ==================================================================
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.template.context_processors.i18n",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# 密码强度
# ==================================================================
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 8},
    },
]

# 通通锁配置
# ==================================================================
TTLOCK_ACCOUNTS = {
    "zhenghe": {
        "CLIENT_ID": "[REDACTED]",
        "CLIENT_SECRET": "[REDACTED]",
        "USERNAME": "[REDACTED]",
        "PASSWORD": "[REDACTED]",
        "BASE_URL": "https://cnapi.ttlock.com",
    },
    "sizuya": {
        "CLIENT_ID": "[REDACTED]",
        "CLIENT_SECRET": "[REDACTED]",
        "USERNAME": "[REDACTED]",
        "PASSWORD": "[REDACTED]",
        "BASE_URL": "https://api.ttlock.com",
    },
}

# 数据库配置
# ==================================================================
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "CONN_MAX_AGE": 60,
        "NAME": os.environ.get("DB_NAME", "passport_db"),
        "USER": os.environ.get("DB_USER", "postgres"),
        "PASSWORD": os.environ.get("DB_PASSWORD", "[REDACTED]"),
        "HOST": os.environ.get("DB_HOST", "192.168.1.2"),
        "PORT": os.environ.get("DB_PORT", "5433"),
    }
}

# 静态文件URL和ROOT
# ==================================================================
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [
    BASE_DIR / "static",
]

# 媒体文件URL和ROOT
# ==================================================================
MEDIA_URL = "/media/"
MEDIA_ROOT = os.environ.get("MEDIA_ROOT", "/code/media")
