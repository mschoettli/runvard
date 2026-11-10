ARG RUNVARD_BASE_IMAGE=nextcloud:34-apache
FROM ${RUNVARD_BASE_IMAGE}

ARG RUNVARD_BASE_IMAGE
LABEL io.runvard.base.image="${RUNVARD_BASE_IMAGE}"

RUN apt-get update \
    && apt-get install -y --no-install-recommends poppler-utils \
    && rm -rf /var/lib/apt/lists/*

ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD php -r '$status = json_decode(file_get_contents("http://127.0.0.1/status.php"), true); exit(($status["installed"] ?? false) && !($status["maintenance"] ?? true) && !($status["needsDbUpgrade"] ?? true) ? 0 : 1);'
