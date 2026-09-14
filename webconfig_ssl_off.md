server
{
    listen 80;
`
    server_name passport.sizuya.net;
    root /www/wwwroot/sizuya.net/passport.sizuya.net;
    #error_page 404/404.html;

    # 宝塔 SSL 证书申请/续签验证配置。
    include /www/server/panel/vhost/nginx/well-known/passport_sizuya_net.conf;

    # ACME 验证文件目录。
    location ^~ /.well-known/ {
        root /www/wwwroot/java_node_ssl;
        try_files $uri =404;
    }

    # 阻止危险文件出现在验证目录。
    if ($uri ~ "^/\.well-known/.*\.(php|jsp|py|js|css|lua|ts|go|zip|tar\.gz|rar|7z|sql|bak)$") {
        return 403;
    }

    # SSL 申请完成前，HTTP 直接代理到 Django。
    location / {
        proxy_pass http://127.0.0.1:33204;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Host $host;
        proxy_set_header X-Forwarded-Port $server_port;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Server $host;
        proxy_set_header REMOTE-HOST $remote_addr;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_connect_timeout 30s;
        proxy_send_timeout 60s;
        proxy_read_timeout 300s;
        proxy_redirect off;
    }

    access_log /www/wwwlogs/passport_sizuya_net.log;
    error_log /www/wwwlogs/passport_sizuya_net.error.log;
}