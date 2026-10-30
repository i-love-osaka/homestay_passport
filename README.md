# Sizuya Passport

基于 Django 的民宿入住登记系统。客人可提交入住信息和护照图片；管理后台提供订单管理、证件导出和 TTLock 智能锁操作。

## 功能

- 多语言入住登记页面和护照图片识别
- 房源、门锁、订单及护照资料管理
- Excel / ZIP 导出
- 可选的 TTLock 动态密码和门锁管理功能

## 使用 Docker Compose 启动

需要安装 Docker 和 Docker Compose。首次启动前复制环境变量模板，并至少替换 Django 密钥和本地数据库密码：

```sh
cp .env.example .env
```

```sh
docker compose up --build -d
docker compose exec web python manage.py migrate
docker compose exec web python manage.py createsuperuser
```

访问 `http://localhost:33204/` 查看登记页面，访问 `http://localhost:33204/admin/` 登录管理后台。端口可通过 `.env` 中的 `WEB_PORT` 修改。

要启用 TTLock 操作，请在 `.env` 中填写对应账号的 `TTLOCK_ZHENGHE_*` 或 `TTLOCK_SIZUYA_*` 凭据。未配置凭据时，普通页面和后台仍可启动，但对应的智能锁操作不可用。

## 配置说明

`.env` 不应提交到版本控制。`.env.example` 为本地开发配置；对外部署前请使用唯一的 `DJANGO_SECRET_KEY`，将 `DJANGO_DEBUG` 设为 `False`，并按实际域名设置 `DJANGO_DOMAIN`、`DJANGO_ALLOWED_HOSTS` 和 `DJANGO_CSRF_TRUSTED_ORIGINS`。生产环境还应使用适当的 Web / WSGI 部署方式、HTTPS 和安全的密钥管理；本 Compose 配置中的 Django `runserver` 仅用于本地开发。

数据库数据保存在 Docker 命名卷 `postgres_data` 中，上传文件位于本地 `media/` 目录。运行中的房源、订单和上传文件均不包含在源代码中。

## 敏感资料

该应用处理护照图片、客人个人资料和门锁凭据。部署者有责任落实访问控制、传输与存储保护、备份策略及适用的数据保护要求。不要将真实客人数据、上传文件或任何生产凭据提交到公开仓库。

## 开源许可

本仓库目前未附带开源许可证。添加许可证之前，请确认你有权公开并授权仓库中的代码、图片、翻译和其他素材。
