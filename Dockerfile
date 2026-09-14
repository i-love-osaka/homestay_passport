FROM python:3.12.12-slim

# 修改：合并环境变量配置，减少 Dockerfile 行数。
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

# 容器里的工作目录
WORKDIR /code

# 安装系统级依赖
RUN set -eux; apt-get update && apt-get install -y --no-install-recommends \
    git \
    libgl1 \
    libglib2.0-0 \
    tesseract-ocr \
    tesseract-ocr-eng \
    tesseract-ocr-osd \
    && rm -rf /var/lib/apt/lists/*

# 先复制依赖清单并安装 Python 包（利用 Docker 缓存）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 再复制项目源码
COPY . .

# 对外暴露端口
EXPOSE 8000

# 启动 Django（开发/测试用，生产建议换成 gunicorn）
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000", "--noreload"]
