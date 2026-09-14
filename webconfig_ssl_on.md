server
{
    # 80 端口只做两件事：放行证书验证、把正常访问跳转到 HTTPS。
    listen 80;

    server_name passport.sizuya.net;
    root /www/wwwroot/sizuya.net/passport.sizuya.net;

    # 宝塔 SSL 证书申请/续签验证配置。保留在 80 端口，避免续签失败。
    include /www/server/panel/vhost/nginx/well-known/passport_sizuya_net.conf;

    # ACME 验证文件目录。^~ 确保不会被下面的 location / 跳转规则抢走。
    location ^~ /.well-known/ {
        root /www/wwwroot/java_node_ssl;
        try_files $uri =404;
    }

    # 验证目录里不应该出现脚本、压缩包或数据库备份。
    if ($uri ~ "^/\.well-known/.*\.(php|jsp|py|js|css|lua|ts|go|zip|tar\.gz|rar|7z|sql|bak)$") {
        return 403;
    }

    # 其余 HTTP 请求全部跳 HTTPS。
    location / {
        return 301 https://$host$request_uri;
    }

    # 日志文件名使用宝塔站点标识，模板复用时替换 passport_sizuya_net。
    access_log /www/wwwlogs/passport_sizuya_net.log;
    error_log  /www/wwwlogs/passport_sizuya_net.error.log;
}

server
{
    # HTTPS 主站。若当前宝塔 Nginx 不支持 HTTP/3，删除 listen 443 quic。
    # 若旧版 Nginx 不支持 http2 on，可改为：listen 443 ssl http2;
    listen 443 ssl;
    listen 443 quic;
    http2 on;

    # =========================================================
    # 站点基础配置
    # =========================================================
    server_name passport.sizuya.net;
    index index.html index.htm default.htm default.html;
    root /www/wwwroot/sizuya.net/passport.sizuya.net;
    #error_page 404/404.html;

    # 宝塔扩展配置与证书验证配置。路径必须写死，不建议改成变量。
    include /www/server/panel/vhost/nginx/extension/passport_sizuya_net/*.conf;
    include /www/server/panel/vhost/nginx/well-known/passport_sizuya_net.conf;

    # =========================================================
    # SSL / HTTP3
    # =========================================================
    # 宝塔证书文件路径，通常由面板自动维护。
    ssl_certificate     /www/server/panel/vhost/cert/passport_sizuya_net/fullchain.pem;
    ssl_certificate_key /www/server/panel/vhost/cert/passport_sizuya_net/privkey.pem;

    # 只启用现代 TLS。TLSv1.0/1.1 已不建议用于公网站点。
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers EECDH+CHACHA20:EECDH+AES128:EECDH+AES256:!aNULL:!MD5:!3DES;
    ssl_prefer_server_ciphers on;
    ssl_session_tickets off;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 10m;

    # HSTS 会让浏览器一年内强制使用 HTTPS。确认 HTTPS 可长期稳定后再上线使用。
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    # 给支持的浏览器广播 HTTP/3 能力。
    add_header Alt-Svc 'h3=":443"; h3-29=":443"' always;
    # 处理用户误用明文 HTTP 访问 443 端口的情况。
    error_page 497 https://$host$request_uri;

    # =========================================================
    # 通用安全响应头
    # =========================================================
    # 隐藏 Nginx 版本号。
    server_tokens off;
    # 允许同源 iframe，兼容 Django Admin。
    add_header X-Frame-Options "SAMEORIGIN" always;
    # 禁止浏览器猜测响应类型。
    add_header X-Content-Type-Options "nosniff" always;
    # 控制 Referer 泄露范围。
    add_header Referrer-Policy "same-origin" always;
    # 默认关闭站点通常不需要的敏感浏览器能力。
    add_header Permissions-Policy "geolocation=(), microphone=(), camera=()" always;

    # =========================================================
    # 压缩
    # =========================================================
    # 仅压缩文本类资源；图片、视频、压缩包无需重复压缩。
    gzip on;
    gzip_comp_level 5;
    gzip_min_length 1024;
    gzip_vary on;
    gzip_proxied any;
    gzip_types
        text/plain
        text/css
        text/xml
        text/javascript
        application/json
        application/javascript
        application/xml
        application/rss+xml
        image/svg+xml;

    # =========================================================
    # 宝塔伪静态 / 重写规则
    # =========================================================
    # 如果宝塔没有生成这个文件，保存配置时报错时可删除本行或创建空文件。
    include /www/server/panel/vhost/rewrite/python_passport_sizuya_net.conf;

    # =========================================================
    # 静态资源
    # =========================================================
    # Django collectstatic 输出目录，对应 settings.STATIC_ROOT。
    location /static/ {
        alias /www/wwwroot/sizuya.net/passport.sizuya.net/staticfiles/;
        access_log off;
        expires 30d;
        try_files $uri =404;
    }

    # 用户上传文件目录，对应 settings.MEDIA_ROOT。
    location /media/ {
        alias /www/wwwroot/sizuya.net/media.sizuya.net/media/;
        access_log off;
        expires 30d;
        try_files $uri =404;
    }

    # 浏览器默认会请求根路径 /favicon.ico，这里映射到静态目录。
    location = /favicon.ico {
        alias /www/wwwroot/sizuya.net/passport.sizuya.net/static/favicon/favicon.ico;
        access_log off;
        expires 30d;
    }

    # 常见 favicon / PWA 图标根路径兼容，避免部分设备不读取 HTML link 时 404。
    location = /apple-touch-icon.png {
        alias /www/wwwroot/sizuya.net/passport.sizuya.net/static/favicon/apple-touch-icon.png;
        access_log off;
        expires 30d;
    }

    location = /favicon-16x16.png {
        alias /www/wwwroot/sizuya.net/passport.sizuya.net/static/favicon/favicon-16x16.png;
        access_log off;
        expires 30d;
    }

    location = /favicon-32x32.png {
        alias /www/wwwroot/sizuya.net/passport.sizuya.net/static/favicon/favicon-32x32.png;
        access_log off;
        expires 30d;
    }

    location = /android-chrome-192x192.png {
        alias /www/wwwroot/sizuya.net/passport.sizuya.net/static/favicon/android-chrome-192x192.png;
        access_log off;
        expires 30d;
    }

    location = /android-chrome-512x512.png {
        alias /www/wwwroot/sizuya.net/passport.sizuya.net/static/favicon/android-chrome-512x512.png;
        access_log off;
        expires 30d;
    }

    location = /site.webmanifest {
        alias /www/wwwroot/sizuya.net/passport.sizuya.net/static/favicon/site.webmanifest;
        access_log off;
        expires 1d;
    }

    # 搜索引擎爬虫规则。
    location = /robots.txt {
        alias /www/wwwroot/sizuya.net/passport.sizuya.net/static/robots.txt;
        access_log off;
        expires 1d;
    }

    # =========================================================
    # 证书申请验证目录
    # =========================================================
    # HTTPS 下也保留验证目录，方便外部校验与宝塔续签。
    location ^~ /.well-known/ {
        root /www/wwwroot/java_node_ssl;
        try_files $uri =404;
    }

    # 与 80 端口一致，阻止危险文件出现在验证目录。
    if ($uri ~ "^/\.well-known/.*\.(php|jsp|py|js|css|lua|ts|go|zip|tar\.gz|rar|7z|sql|bak)$") {
        return 403;
    }

    # =========================================================
    # 安全限制
    # =========================================================
    # 上传体积限制。后台媒体库若要上传更大文件，可按业务需要调高。
    client_max_body_size 50m;

    # 禁止访问版本库、IDE 配置、缓存目录和依赖目录。
    location ~* (^|/)(\.git|\.svn|\.bzr|\.hg|\.vscode|\.idea|\.ssh|\.github|\.npm|\.yarn|\.pnpm|\.cache|\.husky|\.turbo|\.next|\.nuxt|node_modules|runtime|__pycache__|\.pytest_cache|\.mypy_cache)/ {
        return 404;
    }

    # 禁止访问源码配置、依赖清单、日志、备份、数据库导出等敏感文件。
    location ~* (^|/)(\.user\.ini|\.htaccess|\.htpasswd|\.env.*|\.DS_Store|\.gitignore|\.gitattributes|LICENSE|README(\.md)?|CHANGELOG(\.md)?|CONTRIBUTING\.md|TODO\.md|FAQ\.md|docker-compose\.ya?ml|docker\.env|Dockerfile|requirements\.txt|pyproject\.toml|poetry\.lock|Pipfile(\.lock)?|package(-lock)?\.json|yarn\.lock|pnpm-lock\.yaml|composer\.(json|lock)|Cargo\.(toml|lock)|go\.(mod|sum)|pom\.xml|build\.gradle|phpunit\.xml|application(-\w+)?\.(ya?ml|properties)|.*\.(bak|backup|old|tmp|temp|log|sql|sql\.gz|swp|swo))$ {
        return 404;
    }

    # =========================================================
    # 反向代理到 Django
    # =========================================================
    # 33204 是后端服务端口。若 Docker 映射为 33204:8000，就替换为 33204
    location / {
        proxy_pass http://127.0.0.1:33204;

        # 保留真实 Host 和协议，让 Django 生成正确的 URL、CSRF 与安全判断。
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Host $host;
        proxy_set_header X-Forwarded-Port $server_port;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Server $host;
        proxy_set_header REMOTE-HOST $remote_addr;

        # 支持 WebSocket / 长连接升级；普通 HTTP 请求也可以正常工作。
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";

        # 后台操作或大响应时，读取超时适当放宽。
        proxy_connect_timeout 30s;
        proxy_send_timeout 60s;
        proxy_read_timeout 300s;
        proxy_buffering on;
        proxy_redirect off;
    }

    # 日志文件名使用宝塔站点标识，模板复用时替换 passport_sizuya_net。
    access_log /www/wwwlogs/passport_sizuya_net.log;
    error_log  /www/wwwlogs/passport_sizuya_net.error.log;
}
