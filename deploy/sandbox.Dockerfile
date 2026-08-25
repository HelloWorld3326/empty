# 沙箱镜像。刻意做得很薄：agent 需要的是数据加工能力，不是一个完整开发环境。
#
# 镜像里不放任何凭证。数据库查询走网关侧的 run_sql 工具，
# 沙箱拿到的只是结果文件。
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        bash coreutils findutils grep sed gawk jq \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
        pandas==2.2.3 \
        numpy==2.1.3 \
        matplotlib==3.9.2 \
        openpyxl==3.1.5 \
        tabulate==0.9.0

# matplotlib 中文字体。没有它画出来的图全是方框，业务同学会直接判死刑。
RUN apt-get update && apt-get install -y --no-install-recommends fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/* \
    && python -c "import matplotlib.font_manager as fm; fm.fontManager.__init__()"

ENV MPLBACKEND=Agg \
    MPLCONFIGDIR=/tmp/mpl \
    PYTHONDONTWRITEBYTECODE=1

# 非 root 运行，与 Pod securityContext 的 runAsUser 对齐。
RUN useradd -u 10001 -m sandbox
RUN mkdir -p /mnt/user-data/uploads /mnt/user-data/workspace /mnt/user-data/outputs \
    && chown -R 10001:10001 /mnt/user-data
USER 10001
WORKDIR /mnt/user-data/workspace

CMD ["sleep", "infinity"]
