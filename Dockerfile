FROM python:3.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    RUNVARD_PORT=8080 \
    RUNVARD_USER=admin \
    RUNVARD_PASS=runvard

WORKDIR /opt/runvard

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        cifs-utils \
        curl \
        docker.io \
        iproute2 \
        iputils-ping \
        libvirt-clients \
        mount \
        nfs-common \
        openssh-client \
        procps \
        psmisc \
        qemu-utils \
        rsync \
        samba-common-bin \
        smartmontools \
        util-linux \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
COPY wheels ./wheels
RUN python -m pip install --no-cache-dir --no-index --find-links=/opt/runvard/wheels -r requirements.txt

COPY . .

RUN mkdir -p /opt/runvard/data

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/' % os.environ.get('RUNVARD_PORT', '8080'), timeout=3).read()" || exit 1

CMD ["sh", "-c", "exec python -m uvicorn server:app --host 0.0.0.0 --port \"$RUNVARD_PORT\" --workers 1"]
