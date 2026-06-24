FROM python:3.13-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends iproute2 iputils-ping avahi-utils samba-common-bin \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY app.py /app/app.py

ENV PORT=8766 \
    SCAN_CIDRS=auto \
    PORT_RANGE=1-65535 \
    NAME_SOURCES=dns,mdns,netbios \
    DATA_DIR=/data

VOLUME ["/data"]

CMD ["python", "/app/app.py"]
