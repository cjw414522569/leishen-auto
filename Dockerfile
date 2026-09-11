# 零第三方依赖，所以镜像里除了源码什么都不用装，也没有 pip install 这一步
FROM python:3.12-slim

# apt 源默认走阿里云。国内构建快很多；想用官方源就：
#   docker compose build --build-arg APT_MIRROR=deb.debian.org
ARG APT_MIRROR=mirrors.aliyun.com

# cron 按「本机时区」计算，而容器默认是 UTC——不校准的话
# RUN_CRON="0 1 * * *" 会跑在北京时间早上 9 点。
# TZ 要在装 tzdata 之前设好，否则 tzdata 的 postinst 会交互式地问时区。
ENV TZ=Asia/Shanghai

# 装 tzdata，它提供 /usr/share/zoneinfo，TZ 才能真正生效。
# DEBIAN_FRONTEND 必须设成 noninteractive，否则构建可能卡在 tzdata 的选择提示上。
RUN set -eux; \
    export DEBIAN_FRONTEND=noninteractive; \
    for f in /etc/apt/sources.list /etc/apt/sources.list.d/*.sources; do \
        [ -f "$f" ] || continue; \
        sed -i "s|deb.debian.org|${APT_MIRROR}|g" "$f"; \
    done; \
    apt-get update; \
    apt-get install -y --no-install-recommends tzdata; \
    rm -rf /var/lib/apt/lists/*

# 常驻时日志要能实时进 docker logs，不能被 stdout 缓冲挡住
ENV PYTHONUNBUFFERED=1

WORKDIR /app
COPY . /app

# 令牌缓存放 /data，配合 compose 的具名卷可以跨容器重建保留。
# 用非 root 跑，顺便把这两个目录的属主一起改掉——具名卷首次挂载时会继承
# 镜像里该目录的属主，所以这么写之后普通用户才能写进去。
RUN useradd --create-home --uid 1000 app \
    && mkdir -p /data \
    && chown -R app:app /app /data
USER app

# 默认只跑一次。常驻定时由 RUN_CRON 控制，见 docker-compose.yml
CMD ["python", "main.py"]
