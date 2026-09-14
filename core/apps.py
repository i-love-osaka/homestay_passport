from django.apps import AppConfig
from django.contrib.admin.apps import AdminConfig
from django.contrib.auth.apps import AuthConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"

    def ready(self):
        # 将 Django 内置日志归入“系统设置”应用，便于自定义后台统一展示操作日志。
        from django.contrib.admin.models import LogEntry

        LogEntry._meta.app_label = "auth"


class CustomAdminConfig(AdminConfig):
    default_site = "core.admin_site.CustomAdminSite"


class CustomAuthConfig(AuthConfig):
    verbose_name = "系统设置"
