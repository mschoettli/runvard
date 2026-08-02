"""
Apps-Modul – kuratierter App-Store für runvard.
Installiert self-hosted Apps als Docker-Compose-Projekte.

Struktur pro App:  /opt/runvard/data/apps/<id>/docker-compose.yml
Icons:             https://cdn.jsdelivr.net/gh/selfhst/icons/svg/<icon>.svg
Update-Check:      alle 12h im Hintergrund (Cache in apps_updates.json)
"""
import os
import json
import re
import secrets
import socket
import time
import subprocess
import threading

from modules import docker_mgr

try:
    import yaml
except ImportError:
    yaml = None

APPS_DIR = "/opt/runvard/data/apps"
UPDATE_CACHE = "/opt/runvard/data/apps_updates.json"
UPDATE_INTERVAL = 12 * 3600  # 12 Stunden
ICON_BASE = "https://cdn.jsdelivr.net/gh/selfhst/icons/svg"
DEFAULT_RUNVARD_PORT = 8080
PORT_SEARCH_LIMIT = 200

# ──────────────────────────────────────────────────────────────────────────
#  App-Katalog – 100 bekannteste self-hosted Apps
#  Felder: id, name, icon (selfh.st slug), category, desc, port, compose
# ──────────────────────────────────────────────────────────────────────────

CATEGORIES = [
    "Alle", "Media", "Netzwerk", "Produktivität", "AI",
    "Monitoring", "Download", "Home", "Developer", "Datenbank", "Compose",
]


def _c(image, ports=None, volumes=None, env=None, extra="", network_mode="", build=""):
    """Hilfsfunktion: baut ein minimales docker-compose.yml-Template."""
    return {
        "image": image,
        "ports": ports or [],
        "volumes": volumes or [],
        "env": env or {},
        "extra": extra,
        "network_mode": network_mode,
        "build": build,
    }


CATALOG = [
    # ─── Media ──────────────────────────────────────────────────────────
    {"id": "jellyfin", "name": "Jellyfin", "icon": "jellyfin", "category": "Media",
     "desc": "Freier Media-Server für Film, Serien & Musik", "port": 8096,
     "tpl": _c("jellyfin/jellyfin:latest", ["8096:8096"],
               ["./config:/config", "./media:/media"])},
    {"id": "plex", "name": "Plex", "icon": "plex", "category": "Media",
     "desc": "Media-Server mit Premium-Funktionen", "port": 32400,
     "tpl": _c("lscr.io/linuxserver/plex:latest", ["32400:32400"],
               ["./config:/config", "./media:/media"], {"PUID": "1000", "PGID": "1000"})},
    {"id": "emby", "name": "Emby", "icon": "emby", "category": "Media",
     "desc": "Persönlicher Media-Server", "port": 8096,
     "tpl": _c("lscr.io/linuxserver/emby:latest", ["8096:8096"],
               ["./config:/config", "./media:/media"])},
    {"id": "audiobookshelf", "name": "Audiobookshelf", "icon": "audiobookshelf", "category": "Media",
     "desc": "Server für Hörbücher & Podcasts", "port": 13378,
     "tpl": _c("ghcr.io/advplyr/audiobookshelf:latest", ["13378:80"],
               ["./audiobooks:/audiobooks", "./config:/config", "./metadata:/metadata"])},
    {"id": "navidrome", "name": "Navidrome", "icon": "navidrome", "category": "Media",
     "desc": "Moderner Musik-Streaming-Server", "port": 4533,
     "tpl": _c("deluan/navidrome:latest", ["4533:4533"],
               ["./data:/data", "./music:/music:ro"])},
    {"id": "kavita", "name": "Kavita", "icon": "kavita", "category": "Media",
     "desc": "Server für Comics, Manga & E-Books", "port": 5000,
     "tpl": _c("jvmilazz0/kavita:latest", ["5000:5000"],
               ["./manga:/manga", "./config:/kavita/config"])},
    {"id": "calibre-web", "name": "Calibre-Web", "icon": "calibre-web", "category": "Media",
     "desc": "Web-Oberfläche für die E-Book-Bibliothek", "port": 8083,
     "tpl": _c("lscr.io/linuxserver/calibre-web:latest", ["8083:8083"],
               ["./config:/config", "./books:/books"])},
    {"id": "photoprism", "name": "PhotoPrism", "icon": "photoprism", "category": "Media",
     "desc": "KI-gestützte Foto-Verwaltung", "port": 2342,
     "tpl": _c("photoprism/photoprism:latest", ["2342:2342"],
               ["./storage:/photoprism/storage", "./originals:/photoprism/originals"],
               {"PHOTOPRISM_ADMIN_PASSWORD": "insecure"})},
    {"id": "immich", "name": "Immich", "icon": "immich", "category": "Media",
     "desc": "Foto- & Video-Backup in Hochleistung", "port": 2283,
     "tpl": _c("ghcr.io/immich-app/immich-server:release", ["2283:2283"],
               ["./library:/data", "/etc/localtime:/etc/localtime:ro"],
               {
                   "DB_HOSTNAME": "database",
                   "DB_USERNAME": "postgres",
                   "DB_PASSWORD": "postgres",
                   "DB_DATABASE_NAME": "immich",
                   "REDIS_HOSTNAME": "redis",
                   "IMMICH_MACHINE_LEARNING_URL": "http://immich-machine-learning:3003",
               },
               extra="""    depends_on:
      - redis
      - database
  immich-machine-learning:
    image: ghcr.io/immich-app/immich-machine-learning:release
    container_name: immich-machine-learning
    restart: unless-stopped
    volumes:
      - model-cache:/cache
  redis:
    image: docker.io/valkey/valkey:9
    container_name: immich-redis
    restart: unless-stopped
  database:
    image: ghcr.io/immich-app/postgres:14-vectorchord0.4.3-pgvectors0.2.0
    container_name: immich-postgres
    restart: unless-stopped
    environment:
      POSTGRES_PASSWORD: postgres
      POSTGRES_USER: postgres
      POSTGRES_DB: immich
      POSTGRES_INITDB_ARGS: '--data-checksums'
    volumes:
      - ./postgres:/var/lib/postgresql/data
    shm_size: 128mb
volumes:
  model-cache:""")},
    {"id": "tube-archivist", "name": "TubeArchivist", "icon": "invidious", "category": "Media",
     "desc": "Eigenes YouTube-Archiv", "port": 8000,
     "tpl": _c("bbilly1/tubearchivist:latest", ["8000:8000"],
               ["./media:/youtube", "./cache:/cache"])},

    # ─── Netzwerk ───────────────────────────────────────────────────────
    {"id": "pihole", "name": "Pi-hole", "icon": "pi-hole", "category": "Netzwerk",
     "desc": "Netzwerkweiter Ad- & Tracker-Blocker", "port": 80,
     "tpl": _c("pihole/pihole:latest", ["8053:80/tcp", "53:53/tcp", "53:53/udp"],
               ["./etc-pihole:/etc/pihole", "./etc-dnsmasq.d:/etc/dnsmasq.d"],
               {"TZ": "Europe/Zurich", "WEBPASSWORD": "changeme"})},
    {"id": "adguard-home", "name": "AdGuard Home", "icon": "adguard-home", "category": "Netzwerk",
     "desc": "Netzwerkweite Werbe- & Tracking-Sperre", "port": 3000,
     "tpl": _c("adguard/adguardhome:latest", ["3000:3000", "53:53/tcp", "53:53/udp"],
               ["./work:/opt/adguardhome/work", "./conf:/opt/adguardhome/conf"])},
    {"id": "tailscale", "name": "Tailscale", "icon": "tailscale", "category": "Netzwerk",
     "desc": "Zero-Config-VPN für Zugriff von überall", "port": 0,
     "tpl": _c("tailscale/tailscale:latest", [],
               ["./state:/var/lib/tailscale"],
               {"TS_AUTHKEY": "tskey-xxxxx"}, extra="    network_mode: host\n    cap_add:\n      - NET_ADMIN")},
    {"id": "wg-easy", "name": "WireGuard Easy", "icon": "wireguard", "category": "Netzwerk",
     "desc": "WireGuard-VPN mit Web-UI", "port": 51821,
     "tpl": _c("ghcr.io/wg-easy/wg-easy:latest", ["51820:51820/udp", "51821:51821/tcp"],
               ["./config:/etc/wireguard"], {"WG_HOST": "vpn.example.com"})},
    {"id": "nginx-proxy-manager", "name": "Nginx Proxy Manager", "icon": "nginx-proxy-manager", "category": "Netzwerk",
     "desc": "Reverse-Proxy mit SSL & Web-UI", "port": 81,
     "tpl": _c("jc21/nginx-proxy-manager:latest", ["80:80", "81:81", "443:443"],
               ["./data:/data", "./letsencrypt:/etc/letsencrypt"],
               extra='    extra_hosts:\n      - "host.docker.internal:host-gateway"')},
    {"id": "traefik", "name": "Traefik", "icon": "traefik", "category": "Netzwerk",
     "desc": "Cloud-nativer Reverse-Proxy & Load-Balancer", "port": 8080,
     "tpl": _c("traefik:latest", ["80:80", "8080:8080"],
               ["/var/run/docker.sock:/var/run/docker.sock:ro"])},
    {"id": "caddy", "name": "Caddy", "icon": "caddy", "category": "Netzwerk",
     "desc": "Webserver mit automatischem HTTPS", "port": 80,
     "tpl": _c("caddy:latest", ["80:80", "443:443"],
               ["./Caddyfile:/etc/caddy/Caddyfile", "./data:/data"])},
    {"id": "unbound", "name": "Unbound", "icon": "unbound", "category": "Netzwerk",
     "desc": "Validierender DNS-Resolver", "port": 5335,
     "tpl": _c("mvance/unbound:latest", ["5335:53/tcp", "5335:53/udp"],
               ["./config:/opt/unbound/etc/unbound"])},
    {"id": "ddns-updater", "name": "DDNS-Updater", "icon": "ddns-updater", "category": "Netzwerk",
     "desc": "Dynamisches DNS automatisch aktualisieren", "port": 8000,
     "tpl": _c("qmcgaw/ddns-updater:latest", ["8000:8000"], ["./data:/updater/data"])},
    {"id": "speedtest-tracker", "name": "Speedtest Tracker", "icon": "speedtest-tracker", "category": "Netzwerk",
     "desc": "Internet-Geschwindigkeit über Zeit verfolgen", "port": 8080,
     "tpl": _c("lscr.io/linuxserver/speedtest-tracker:latest", ["8080:80"],
               ["./config:/config"], {"APP_KEY": "base64:changeme"})},
    {"id": "omada-controller", "name": "Omada Controller", "icon": "tp-link", "category": "Netzwerk",
     "desc": "TP-Link Omada Netzwerk-Controller", "port": 8088,
     "tpl": _c("mbentley/omada-controller:latest", ["8088:8088", "8043:8043"],
               ["./data:/opt/tplink/EAPController/data"])},

    # ─── Produktivität ──────────────────────────────────────────────────
    {"id": "nextcloud", "name": "Nextcloud", "icon": "nextcloud", "category": "Produktivität",
     "desc": "Eigene private Cloud für Dateien & mehr", "port": 8080,
     "tpl": _c("nextcloud:latest", ["8080:80"],
               ["./data:/var/www/html"])},
    {"id": "vaultwarden", "name": "Vaultwarden", "icon": "vaultwarden", "category": "Produktivität",
     "desc": "Bitwarden-kompatibler Passwort-Manager", "port": 80,
     "tpl": _c("vaultwarden/server:latest", ["8000:80"],
               ["./data:/data"], {"WEBSOCKET_ENABLED": "true"})},
    {"id": "paperless-ngx", "name": "Paperless-ngx", "icon": "paperless-ngx", "category": "Produktivität",
     "desc": "Digitales Dokumenten-Management", "port": 8000,
     "tpl": _c("ghcr.io/paperless-ngx/paperless-ngx:latest", ["8000:8000"],
               ["./data:/usr/src/paperless/data", "./media:/usr/src/paperless/media",
                "./consume:/usr/src/paperless/consume"])},
    {"id": "papervard", "name": "Papervard", "icon": "/static/papervard.svg",
     "category": "Produktivität",
     "desc": "Lokales Dokumentenarchiv für Familien und kleine Teams", "port": 3000,
     "first_party": True,
     "compose_builder": "papervard",
     "managed_directories": ["config", "data"],
     "preflight_ports": True,
     "install_timeout": 900,
     "tpl": _c("ghcr.io/mschoettli/papervard:latest", ["3000:3000", "8081:80"],
               ["./config:/config", "./data:/data"])},
    {"id": "joplin-server", "name": "Joplin Server", "icon": "joplin", "category": "Produktivität",
     "desc": "Synchronisierungs-Server für Joplin-Notizen", "port": 22300,
     "tpl": _c("joplin/server:latest", ["22300:22300"], [],
               {"APP_BASE_URL": "http://localhost:22300"})},
    {"id": "trilium", "name": "Trilium Notes", "icon": "trilium-notes", "category": "Produktivität",
     "desc": "Hierarchische Notiz-App für Wissensbasen", "port": 8080,
     "tpl": _c("triliumnext/notes:latest", ["8080:8080"], ["./data:/home/node/trilium-data"])},
    {"id": "affine", "name": "AFFiNE", "icon": "affine", "category": "Produktivität",
     "desc": "Open-Source-Alternative zu Notion & Miro", "port": 3010,
     "tpl": _c("ghcr.io/toeverything/affine-graphql:stable", ["3010:3010"],
               ["./config:/root/.affine/config", "./storage:/root/.affine/storage"])},
    {"id": "outline", "name": "Outline", "icon": "outline", "category": "Produktivität",
     "desc": "Team-Wissensdatenbank & Wiki", "port": 3000,
     "tpl": _c("outlinewiki/outline:latest", ["3000:3000"], [],
               {"SECRET_KEY": "changeme", "URL": "http://localhost:3000"})},
    {"id": "bookstack", "name": "BookStack", "icon": "bookstack", "category": "Produktivität",
     "desc": "Plattform für Dokumentation & Wikis", "port": 6875,
     "tpl": _c("lscr.io/linuxserver/bookstack:latest", ["6875:80"],
               ["./config:/config"], {"APP_URL": "http://localhost:6875"})},
    {"id": "mealie", "name": "Mealie", "icon": "mealie", "category": "Produktivität",
     "desc": "Rezept-Manager & Essensplaner", "port": 9000,
     "tpl": _c("ghcr.io/mealie-recipes/mealie:latest", ["9000:9000"],
               ["./data:/app/data"])},
    {"id": "wallabag", "name": "Wallabag", "icon": "wallabag", "category": "Produktivität",
     "desc": "Artikel speichern & später lesen", "port": 80,
     "tpl": _c("wallabag/wallabag:latest", ["8080:80"], ["./data:/var/www/wallabag/data"])},
    {"id": "linkding", "name": "Linkding", "icon": "linkding", "category": "Produktivität",
     "desc": "Minimalistischer Bookmark-Manager", "port": 9090,
     "tpl": _c("sissbruecker/linkding:latest", ["9090:9090"], ["./data:/etc/linkding/data"])},
    {"id": "freshrss", "name": "FreshRSS", "icon": "freshrss", "category": "Produktivität",
     "desc": "Selbst-gehosteter RSS-Feed-Aggregator", "port": 80,
     "tpl": _c("freshrss/freshrss:latest", ["8080:80"],
               ["./data:/var/www/FreshRSS/data", "./extensions:/var/www/FreshRSS/extensions"])},
    {"id": "stirling-pdf", "name": "Stirling-PDF", "icon": "stirling-pdf", "category": "Produktivität",
     "desc": "Werkzeuge zum Bearbeiten von PDFs", "port": 8080,
     "tpl": _c("frooodle/s-pdf:latest", ["8080:8080"], ["./data:/usr/share/tessdata"])},
    {"id": "docuseal", "name": "DocuSeal", "icon": "docuseal", "category": "Produktivität",
     "desc": "Dokumente digital signieren", "port": 3000,
     "tpl": _c("docuseal/docuseal:latest", ["3000:3000"], ["./data:/data"])},
    {"id": "actual", "name": "Actual Budget", "icon": "actual-budget", "category": "Produktivität",
     "desc": "Privates Haushaltsbuch & Budget-Tool", "port": 5006,
     "tpl": _c("actualbudget/actual-server:latest", ["5006:5006"], ["./data:/data"])},
    {"id": "firefly-iii", "name": "Firefly III", "icon": "firefly-iii", "category": "Produktivität",
     "desc": "Persönliche Finanzverwaltung", "port": 8080,
     "tpl": _c("fireflyiii/core:latest", ["8080:8080"], ["./upload:/var/www/html/storage/upload"],
               {"APP_KEY": "changemechangemechangemechangeme"})},
    {"id": "ghostfolio", "name": "Ghostfolio", "icon": "ghostfolio", "category": "Produktivität",
     "desc": "Portfolio-Tracker für Vermögenswerte", "port": 3333,
     "tpl": _c("ghostfolio/ghostfolio:latest", ["3333:3333"], [])},

    # ─── AI ─────────────────────────────────────────────────────────────
    {"id": "ollama", "name": "Ollama", "icon": "ollama", "category": "AI",
     "desc": "Lokale LLMs wie Llama & DeepSeek hosten", "port": 11434,
     "tpl": _c("ollama/ollama:latest", ["11434:11434"], ["./data:/root/.ollama"])},
    {"id": "open-webui", "name": "Open WebUI", "icon": "open-webui", "category": "AI",
     "desc": "Chat-Oberfläche für lokale LLMs", "port": 8080,
     "tpl": _c("ghcr.io/open-webui/open-webui:main", ["8080:8080"],
               ["./data:/app/backend/data"])},
    {"id": "anything-llm", "name": "AnythingLLM", "icon": "anythingllm", "category": "AI",
     "desc": "KI-Workspace für Chat, Dokumente & Agenten", "port": 3001,
     "tpl": _c("mintplexlabs/anythingllm:latest", ["3001:3001"], ["./storage:/app/server/storage"])},
    {"id": "stable-diffusion", "name": "Stable Diffusion", "icon": "automatic1111", "category": "AI",
     "desc": "Bilder mit KI generieren (AUTOMATIC1111)", "port": 7860,
     "tpl": _c("universonic/stable-diffusion-webui:latest", ["7860:7860"],
               ["./data:/data", "./outputs:/output"])},
    {"id": "comfyui", "name": "ComfyUI", "icon": "comfyui", "category": "AI",
     "desc": "Node-basierte Stable-Diffusion-Oberfläche", "port": 8188,
     "tpl": _c("yanwk/comfyui-boot:latest", ["8188:8188"], ["./data:/root"])},
    {"id": "localai", "name": "LocalAI", "icon": "localai", "category": "AI",
     "desc": "OpenAI-kompatible lokale API", "port": 8080,
     "tpl": _c("localai/localai:latest", ["8080:8080"], ["./models:/models"])},
    {"id": "perplexica", "name": "Perplexica", "icon": "perplexica", "category": "AI",
     "desc": "KI-gestützte Suchmaschine", "port": 3000,
     "tpl": _c("itzcrazykns1337/perplexica:latest", ["3000:3000"], ["./data:/home/perplexica/data"])},
    {"id": "n8n", "name": "n8n", "icon": "n8n", "category": "AI",
     "desc": "Workflow-Automatisierung mit KI-Knoten", "port": 5678,
     "tpl": _c("n8nio/n8n:latest", ["5678:5678"], ["./data:/home/node/.n8n"])},

    # ─── Monitoring ─────────────────────────────────────────────────────
    {"id": "grafana", "name": "Grafana", "icon": "grafana", "category": "Monitoring",
     "desc": "Dashboards & Visualisierung von Metriken", "port": 3000,
     "tpl": _c("grafana/grafana:latest", ["3000:3000"], ["./data:/var/lib/grafana"])},
    {"id": "prometheus", "name": "Prometheus", "icon": "prometheus", "category": "Monitoring",
     "desc": "Metrik-Sammlung & Zeitreihen-Datenbank", "port": 9090,
     "tpl": _c("prom/prometheus:latest", ["9090:9090"], ["./data:/prometheus"])},
    {"id": "uptime-kuma", "name": "Uptime Kuma", "icon": "uptime-kuma", "category": "Monitoring",
     "desc": "Überwachung der Verfügbarkeit von Diensten", "port": 3001,
     "tpl": _c("louislam/uptime-kuma:1", ["3001:3001"], ["./data:/app/data"])},
    {"id": "portainer", "name": "Portainer", "icon": "portainer", "category": "Monitoring",
     "desc": "Web-Oberfläche zur Docker-Verwaltung", "port": 9443,
     "tpl": _c("portainer/portainer-ce:latest", ["9443:9443", "8000:8000"],
               ["/var/run/docker.sock:/var/run/docker.sock", "./data:/data"])},
    {"id": "dozzle", "name": "Dozzle", "icon": "dozzle", "category": "Monitoring",
     "desc": "Echtzeit-Logs aller Container im Browser", "port": 8080,
     "tpl": _c("amir20/dozzle:latest", ["8080:8080"],
               ["/var/run/docker.sock:/var/run/docker.sock"])},
    {"id": "netdata", "name": "Netdata", "icon": "netdata", "category": "Monitoring",
     "desc": "Echtzeit-Performance-Monitoring", "port": 19999,
     "tpl": _c("netdata/netdata:latest", ["19999:19999"],
               ["/proc:/host/proc:ro", "/sys:/host/sys:ro"], extra="    cap_add:\n      - SYS_PTRACE")},
    {"id": "beszel", "name": "Beszel", "icon": "beszel", "category": "Monitoring",
     "desc": "Leichtgewichtiges Server-Monitoring", "port": 8090,
     "tpl": _c("henrygd/beszel:latest", ["8090:8090"], ["./data:/beszel_data"])},
    {"id": "scrutiny", "name": "Scrutiny", "icon": "scrutiny", "category": "Monitoring",
     "desc": "Festplatten-Gesundheit (S.M.A.R.T.) überwachen", "port": 8080,
     "tpl": _c("ghcr.io/analogj/scrutiny:master-omnibus", ["8080:8080"],
               ["./config:/opt/scrutiny/config", "./influxdb:/opt/scrutiny/influxdb"])},
    {"id": "glances", "name": "Glances", "icon": "glances", "category": "Monitoring",
     "desc": "System-Übersicht im Terminal & Web", "port": 61208,
     "tpl": _c("nicolargo/glances:latest", ["61208:61208"],
               ["/var/run/docker.sock:/var/run/docker.sock:ro"], {"GLANCES_OPT": "-w"})},

    # ─── Download ───────────────────────────────────────────────────────
    {"id": "qbittorrent", "name": "qBittorrent", "icon": "qbittorrent", "category": "Download",
     "desc": "BitTorrent-Client mit Web-Oberfläche", "port": 8080,
     "tpl": _c("lscr.io/linuxserver/qbittorrent:latest", ["8080:8080", "6881:6881"],
               ["./config:/config", "./downloads:/downloads"],
               {"PUID": "1000", "PGID": "1000", "WEBUI_PORT": "8080"})},
    {"id": "transmission", "name": "Transmission", "icon": "transmission", "category": "Download",
     "desc": "Leichtgewichtiger BitTorrent-Client", "port": 9091,
     "tpl": _c("lscr.io/linuxserver/transmission:latest", ["9091:9091", "51413:51413"],
               ["./config:/config", "./downloads:/downloads"])},
    {"id": "sabnzbd", "name": "SABnzbd", "icon": "sabnzbd", "category": "Download",
     "desc": "Usenet-Downloader (NZB)", "port": 8080,
     "tpl": _c("lscr.io/linuxserver/sabnzbd:latest", ["8080:8080"],
               ["./config:/config", "./downloads:/downloads"])},
    {"id": "sonarr", "name": "Sonarr", "icon": "sonarr", "category": "Download",
     "desc": "Automatische Serien-Verwaltung", "port": 8989,
     "tpl": _c("lscr.io/linuxserver/sonarr:latest", ["8989:8989"],
               ["./config:/config", "./tv:/tv", "./downloads:/downloads"])},
    {"id": "radarr", "name": "Radarr", "icon": "radarr", "category": "Download",
     "desc": "Automatische Film-Verwaltung", "port": 7878,
     "tpl": _c("lscr.io/linuxserver/radarr:latest", ["7878:7878"],
               ["./config:/config", "./movies:/movies", "./downloads:/downloads"])},
    {"id": "prowlarr", "name": "Prowlarr", "icon": "prowlarr", "category": "Download",
     "desc": "Indexer-Manager für *arr-Apps", "port": 9696,
     "tpl": _c("lscr.io/linuxserver/prowlarr:latest", ["9696:9696"], ["./config:/config"])},
    {"id": "bazarr", "name": "Bazarr", "icon": "bazarr", "category": "Download",
     "desc": "Untertitel automatisch herunterladen", "port": 6767,
     "tpl": _c("lscr.io/linuxserver/bazarr:latest", ["6767:6767"],
               ["./config:/config", "./movies:/movies", "./tv:/tv"])},
    {"id": "lidarr", "name": "Lidarr", "icon": "lidarr", "category": "Download",
     "desc": "Automatische Musik-Verwaltung", "port": 8686,
     "tpl": _c("lscr.io/linuxserver/lidarr:latest", ["8686:8686"],
               ["./config:/config", "./music:/music", "./downloads:/downloads"])},
    {"id": "jellyseerr", "name": "Jellyseerr", "icon": "jellyseerr", "category": "Download",
     "desc": "Anfragen-Verwaltung für Jellyfin/Plex", "port": 5055,
     "tpl": _c("fallenbagel/jellyseerr:latest", ["5055:5055"], ["./config:/app/config"])},
    {"id": "metube", "name": "MeTube", "icon": "metube", "category": "Download",
     "desc": "YouTube-Downloader mit Web-UI (yt-dlp)", "port": 8081,
     "tpl": _c("ghcr.io/alexta69/metube:latest", ["8081:8081"], ["./downloads:/downloads"])},
    {"id": "pinchflat", "name": "Pinchflat", "icon": "pinchflat", "category": "Download",
     "desc": "YouTube-Medien automatisch archivieren", "port": 8945,
     "tpl": _c("ghcr.io/kieraneglin/pinchflat:latest", ["8945:8945"],
               ["./config:/config", "./downloads:/downloads"])},

    # ─── Home & Automation ──────────────────────────────────────────────
    {"id": "home-assistant", "name": "Home Assistant", "icon": "home-assistant", "category": "Home",
     "desc": "Heim-Automatisierung mit Fokus auf Privatsphäre", "port": 8123,
     "tpl": _c("ghcr.io/home-assistant/home-assistant:stable", ["8123:8123"],
               ["./config:/config"], extra="    network_mode: host\n    privileged: true")},
    {"id": "node-red", "name": "Node-RED", "icon": "node-red", "category": "Home",
     "desc": "Visuelle Programmierung für IoT-Flows", "port": 1880,
     "tpl": _c("nodered/node-red:latest", ["1880:1880"], ["./data:/data"])},
    {"id": "zigbee2mqtt", "name": "Zigbee2MQTT", "icon": "zigbee2mqtt", "category": "Home",
     "desc": "Zigbee-Geräte ohne Hersteller-Bridge nutzen", "port": 8080,
     "tpl": _c("koenkk/zigbee2mqtt:latest", ["8080:8080"], ["./data:/app/data"],
               extra="    devices:\n      - /dev/ttyACM0:/dev/ttyACM0")},
    {"id": "esphome", "name": "ESPHome", "icon": "esphome", "category": "Home",
     "desc": "ESP-Mikrocontroller per YAML konfigurieren", "port": 6052,
     "tpl": _c("ghcr.io/esphome/esphome:latest", ["6052:6052"], ["./config:/config"])},
    {"id": "mosquitto", "name": "Mosquitto", "icon": "mosquitto", "category": "Home",
     "desc": "MQTT-Broker für IoT-Nachrichten", "port": 1883,
     "tpl": _c("eclipse-mosquitto:latest", ["1883:1883", "9001:9001"],
               ["./config:/mosquitto/config", "./data:/mosquitto/data"])},
    {"id": "frigate", "name": "Frigate", "icon": "frigate", "category": "Home",
     "desc": "NVR mit Echtzeit-Objekterkennung", "port": 5000,
     "tpl": _c("ghcr.io/blakeblackshear/frigate:stable", ["5000:5000", "8554:8554"],
               ["./config:/config", "./storage:/media/frigate"], extra="    privileged: true")},
    {"id": "scrypted", "name": "Scrypted", "icon": "scrypted", "category": "Home",
     "desc": "Smart-Home-Video-Plattform (HomeKit etc.)", "port": 11080,
     "tpl": _c("ghcr.io/koush/scrypted:latest", ["11080:11080"],
               ["./volume:/server/volume"], extra="    network_mode: host")},
    {"id": "homebridge", "name": "Homebridge", "icon": "homebridge", "category": "Home",
     "desc": "Nicht-HomeKit-Geräte in Apple Home einbinden", "port": 8581,
     "tpl": _c("homebridge/homebridge:latest", ["8581:8581"],
               ["./data:/homebridge"], extra="    network_mode: host")},

    # ─── Developer ──────────────────────────────────────────────────────
    {"id": "gitea", "name": "Gitea", "icon": "gitea", "category": "Developer",
     "desc": "Leichtgewichtiger Git-Server mit Web-UI", "port": 3000,
     "tpl": _c("gitea/gitea:latest", ["3000:3000", "2222:22"], ["./data:/data"],
               {"USER_UID": "1000", "USER_GID": "1000"})},
    {"id": "forgejo", "name": "Forgejo", "icon": "forgejo", "category": "Developer",
     "desc": "Community-getriebener Git-Server", "port": 3000,
     "tpl": _c("codeberg.org/forgejo/forgejo:latest", ["3000:3000", "2222:22"],
               ["./data:/data"])},
    {"id": "gitlab", "name": "GitLab CE", "icon": "gitlab", "category": "Developer",
     "desc": "Komplette DevOps-Plattform", "port": 80,
     "tpl": _c("gitlab/gitlab-ce:latest", ["8080:80", "8443:443", "2222:22"],
               ["./config:/etc/gitlab", "./logs:/var/log/gitlab", "./data:/var/opt/gitlab"])},
    {"id": "code-server", "name": "code-server", "icon": "coder", "category": "Developer",
     "desc": "VS Code im Browser", "port": 8443,
     "tpl": _c("lscr.io/linuxserver/code-server:latest", ["8443:8443"],
               ["./config:/config"], {"PUID": "1000", "PGID": "1000"})},
    {"id": "gitea-runner", "name": "Drone CI", "icon": "drone-ci", "category": "Developer",
     "desc": "Container-native CI/CD-Plattform", "port": 8000,
     "tpl": _c("drone/drone:latest", ["8000:80"], ["./data:/data"],
               {"DRONE_RPC_SECRET": "changeme"})},
    {"id": "woodpecker", "name": "Woodpecker CI", "icon": "woodpecker-ci", "category": "Developer",
     "desc": "Einfache CI/CD-Engine", "port": 8000,
     "tpl": _c("woodpeckerci/woodpecker-server:latest", ["8000:8000"], ["./data:/var/lib/woodpecker"])},
    {"id": "verdaccio", "name": "Verdaccio", "icon": "verdaccio", "category": "Developer",
     "desc": "Leichtgewichtige private npm-Registry", "port": 4873,
     "tpl": _c("verdaccio/verdaccio:latest", ["4873:4873"], ["./storage:/verdaccio/storage"])},
    {"id": "it-tools", "name": "IT-Tools", "icon": "it-tools", "category": "Developer",
     "desc": "Sammlung praktischer Entwickler-Tools", "port": 8080,
     "tpl": _c("corentinth/it-tools:latest", ["8080:80"])},
    {"id": "excalidraw", "name": "Excalidraw", "icon": "excalidraw", "category": "Developer",
     "desc": "Virtuelles Whiteboard für Skizzen", "port": 80,
     "tpl": _c("excalidraw/excalidraw:latest", ["8080:80"])},
    {"id": "uptime-uptrace", "name": "Dockge", "icon": "dockge", "category": "Developer",
     "desc": "Eleganter Docker-Compose-Manager", "port": 5001,
     "tpl": _c("louislam/dockge:1", ["5001:5001"],
               ["/var/run/docker.sock:/var/run/docker.sock", "./data:/app/data"])},

    # ─── Datenbank ──────────────────────────────────────────────────────
    {"id": "postgres", "name": "PostgreSQL", "icon": "postgresql", "category": "Datenbank",
     "desc": "Leistungsstarke relationale Datenbank", "port": 5432,
     "tpl": _c("postgres:16", ["5432:5432"], ["./data:/var/lib/postgresql/data"],
               {"POSTGRES_PASSWORD": "changeme"})},
    {"id": "mariadb", "name": "MariaDB", "icon": "mariadb", "category": "Datenbank",
     "desc": "Beliebte MySQL-kompatible Datenbank", "port": 3306,
     "tpl": _c("mariadb:latest", ["3306:3306"], ["./data:/var/lib/mysql"],
               {"MARIADB_ROOT_PASSWORD": "changeme"})},
    {"id": "mysql", "name": "MySQL", "icon": "mysql", "category": "Datenbank",
     "desc": "Weltweit verbreitete relationale Datenbank", "port": 3306,
     "tpl": _c("mysql:latest", ["3306:3306"], ["./data:/var/lib/mysql"],
               {"MYSQL_ROOT_PASSWORD": "changeme"})},
    {"id": "redis", "name": "Redis", "icon": "redis", "category": "Datenbank",
     "desc": "In-Memory-Datenspeicher & Cache", "port": 6379,
     "tpl": _c("redis:latest", ["6379:6379"], ["./data:/data"])},
    {"id": "mongodb", "name": "MongoDB", "icon": "mongodb", "category": "Datenbank",
     "desc": "Dokumentenorientierte NoSQL-Datenbank", "port": 27017,
     "tpl": _c("mongo:latest", ["27017:27017"], ["./data:/data/db"])},
    {"id": "influxdb", "name": "InfluxDB", "icon": "influxdb", "category": "Datenbank",
     "desc": "Zeitreihen-Datenbank für Metriken", "port": 8086,
     "tpl": _c("influxdb:latest", ["8086:8086"], ["./data:/var/lib/influxdb2"])},
    {"id": "pgadmin", "name": "pgAdmin", "icon": "pgadmin", "category": "Datenbank",
     "desc": "Web-Verwaltung für PostgreSQL", "port": 80,
     "tpl": _c("dpage/pgadmin4:latest", ["8080:80"], ["./data:/var/lib/pgadmin"],
               {"PGADMIN_DEFAULT_EMAIL": "admin@example.com", "PGADMIN_DEFAULT_PASSWORD": "changeme"})},
    {"id": "phpmyadmin", "name": "phpMyAdmin", "icon": "phpmyadmin", "category": "Datenbank",
     "desc": "Web-Verwaltung für MySQL/MariaDB", "port": 80,
     "tpl": _c("phpmyadmin:latest", ["8080:80"], [], {"PMA_ARBITRARY": "1"})},
    {"id": "adminer", "name": "Adminer", "icon": "adminer", "category": "Datenbank",
     "desc": "Datenbank-Verwaltung in einer Datei", "port": 8080,
     "tpl": _c("adminer:latest", ["8080:8080"])},

    # ─── Sonstiges / beliebt ────────────────────────────────────────────
    {"id": "homepage", "name": "Homepage", "icon": "homepage", "category": "Produktivität",
     "desc": "Modernes, anpassbares Start-Dashboard", "port": 3000,
     "tpl": _c("ghcr.io/gethomepage/homepage:latest", ["3000:3000"], ["./config:/app/config"])},
    {"id": "dashy", "name": "Dashy", "icon": "homarr", "category": "Produktivität",
     "desc": "Feature-reiches Dashboard für den Homelab", "port": 8080,
     "tpl": _c("lissy93/dashy:latest", ["8080:8080"], ["./config:/app/user-data"])},
    {"id": "vikunja", "name": "Vikunja", "icon": "vikunja", "category": "Produktivität",
     "desc": "To-Do-App & Aufgaben-Verwaltung", "port": 3456,
     "tpl": _c("vikunja/vikunja:latest", ["3456:3456"], ["./files:/app/vikunja/files"])},
    {"id": "planka", "name": "Planka", "icon": "planka", "category": "Produktivität",
     "desc": "Kanban-Board (Trello-Alternative)", "port": 3000,
     "tpl": _c("ghcr.io/plankanban/planka:latest", ["3000:1337"],
               ["./avatars:/app/public/user-avatars", "./attachments:/app/private/attachments"])},
    {"id": "focalboard", "name": "Focalboard", "icon": "focalboard", "category": "Produktivität",
     "desc": "Projekt- & Aufgaben-Management", "port": 8000,
     "tpl": _c("mattermost/focalboard:latest", ["8000:8000"], ["./data:/opt/focalboard/data"])},
    {"id": "syncthing", "name": "Syncthing", "icon": "syncthing", "category": "Produktivität",
     "desc": "Dezentrale Datei-Synchronisation", "port": 8384,
     "tpl": _c("lscr.io/linuxserver/syncthing:latest", ["8384:8384", "22000:22000"],
               ["./config:/config", "./data:/data"])},
    {"id": "filebrowser", "name": "File Browser", "icon": "filebrowser-quantum", "category": "Produktivität",
     "desc": "Web-Datei-Manager", "port": 80,
     "tpl": _c("filebrowser/filebrowser:latest", ["8080:80"],
               ["./srv:/srv", "./database.db:/database.db"])},
    {"id": "seafile", "name": "Seafile", "icon": "seafile", "category": "Produktivität",
     "desc": "Datei-Sync & -Sharing mit hoher Performance", "port": 80,
     "tpl": _c("seafileltd/seafile-mc:latest", ["8080:80"], ["./data:/shared"])},
    {"id": "kasm", "name": "Kasm Workspaces", "icon": "kasm-workspaces", "category": "Developer",
     "desc": "Streamed Container-Desktops im Browser", "port": 6901,
     "tpl": _c("lscr.io/linuxserver/kasm:latest", ["6901:6901", "443:443"],
               ["./data:/opt"], extra="    privileged: true")},
    {"id": "guacamole", "name": "Apache Guacamole", "icon": "apache-guacamole", "category": "Netzwerk",
     "desc": "Clientloser Remote-Desktop-Zugriff (RDP/VNC/SSH)", "port": 8080,
     "tpl": _c("guacamole/guacamole:latest", ["8080:8080"])},
    {"id": "wireguard-ui", "name": "Whoogle", "icon": "searxng", "category": "Netzwerk",
     "desc": "Privatsphäre-freundliche Google-Suche", "port": 5000,
     "tpl": _c("benbusby/whoogle-search:latest", ["5000:5000"])},
    {"id": "searxng", "name": "SearXNG", "icon": "searxng", "category": "Netzwerk",
     "desc": "Datenschutzfreundliche Meta-Suchmaschine", "port": 8080,
     "tpl": _c("searxng/searxng:latest", ["8080:8080"], ["./config:/etc/searxng"])},
    {"id": "changedetection", "name": "changedetection.io", "icon": "changedetection", "category": "Monitoring",
     "desc": "Webseiten auf Änderungen überwachen", "port": 5000,
     "tpl": _c("ghcr.io/dgtlmoon/changedetection.io:latest", ["5000:5000"],
               ["./data:/datastore"])},
    {"id": "mealie2", "name": "Tandoor", "icon": "tandoor-recipes", "category": "Produktivität",
     "desc": "Rezept-Manager mit Einkaufslisten", "port": 8080,
     "tpl": _c("vabene1111/recipes:latest", ["8080:80"],
               ["./staticfiles:/opt/recipes/staticfiles", "./mediafiles:/opt/recipes/mediafiles"])},
    {"id": "mattermost", "name": "Mattermost", "icon": "mattermost", "category": "Produktivität",
     "desc": "Team-Chat (Slack-Alternative)", "port": 8065,
     "tpl": _c("mattermost/mattermost-team-edition:latest", ["8065:8065"],
               ["./config:/mattermost/config", "./data:/mattermost/data"])},
    {"id": "rocketchat", "name": "Rocket.Chat", "icon": "rocket-chat", "category": "Produktivität",
     "desc": "Team-Kommunikationsplattform", "port": 3000,
     "tpl": _c("rocket.chat:latest", ["3000:3000"], ["./uploads:/app/uploads"])},
    {"id": "minio", "name": "MinIO", "icon": "minio", "category": "Datenbank",
     "desc": "S3-kompatibler Objektspeicher", "port": 9001,
     "tpl": _c("minio/minio:latest", ["9000:9000", "9001:9001"], ["./data:/data"],
               {"MINIO_ROOT_USER": "admin", "MINIO_ROOT_PASSWORD": "changeme"},
               extra='    command: server /data --console-address ":9001"')},
    {"id": "duplicati", "name": "Duplicati", "icon": "duplicati", "category": "Produktivität",
     "desc": "Verschlüsselte Backups in die Cloud", "port": 8200,
     "tpl": _c("lscr.io/linuxserver/duplicati:latest", ["8200:8200"],
               ["./config:/config", "./backups:/backups", "./source:/source"])},
]


# ──────────────────────────────────────────────────────────────────────────
#  Compose generation
# ──────────────────────────────────────────────────────────────────────────

def _runvard_port():
    try:
        return int(os.environ.get("RUNVARD_PORT", DEFAULT_RUNVARD_PORT))
    except (TypeError, ValueError):
        return DEFAULT_RUNVARD_PORT


def _published_port(port_mapping):
    if isinstance(port_mapping, dict):
        published = port_mapping.get("published")
        try:
            return int(str(published))
        except (TypeError, ValueError):
            return None

    spec = str(port_mapping).strip().strip("\"'")
    base = spec.split("/", 1)[0]
    parts = base.rsplit(":", 2)
    if len(parts) == 1:
        return None
    host_port = parts[0] if len(parts) == 2 else parts[-2]
    try:
        return int(host_port)
    except ValueError:
        return None


def _replace_published_port(port_mapping, host_port):
    if isinstance(port_mapping, dict):
        updated = dict(port_mapping)
        updated["published"] = host_port
        return updated

    spec = str(port_mapping).strip().strip("\"'")
    base, sep, proto = spec.partition("/")
    parts = base.rsplit(":", 2)
    if len(parts) == 2:
        updated = f"{host_port}:{parts[1]}"
    elif len(parts) == 3:
        updated = f"{parts[0]}:{host_port}:{parts[2]}"
    else:
        return spec
    return f"{updated}{sep}{proto}" if sep else updated


def _port_entries_from_compose(content):
    if yaml is not None:
        try:
            data = yaml.safe_load(content) or {}
        except Exception:
            data = {}
        services = data.get("services") if isinstance(data, dict) else {}
        if isinstance(services, dict):
            entries = []
            for service in services.values():
                if isinstance(service, dict):
                    ports = service.get("ports") or []
                    if isinstance(ports, list):
                        entries.extend(ports)
            return entries

    return re.findall(r"^\s*-\s*['\"]?([^'\"\n#]+)['\"]?", content, re.MULTILINE)


def _host_ports_from_compose(content):
    ports = set()
    for entry in _port_entries_from_compose(content):
        port = _published_port(entry)
        if port:
            ports.add(port)
    env_port = _environment_port_from_compose(content)
    if env_port:
        ports.add(env_port)
    return ports


def _first_host_port_from_compose(content, fallback=0):
    for entry in _port_entries_from_compose(content):
        port = _published_port(entry)
        if port:
            return port
    env_port = _environment_port_from_compose(content)
    if env_port:
        return env_port
    return fallback


def _environment_port_from_compose(content):
    if yaml is not None:
        try:
            data = yaml.safe_load(content) or {}
        except Exception:
            data = {}
        services = data.get("services") if isinstance(data, dict) else {}
        if isinstance(services, dict):
            for service in services.values():
                if not isinstance(service, dict):
                    continue
                env = service.get("environment") or {}
                if isinstance(env, dict):
                    value = env.get("PORT")
                    try:
                        return int(str(value))
                    except (TypeError, ValueError):
                        continue
                if isinstance(env, list):
                    for item in env:
                        key, sep, value = str(item).partition("=")
                        if sep and key == "PORT":
                            try:
                                return int(value)
                            except ValueError:
                                continue

    in_env = False
    env_indent = None
    for raw in str(content or "").splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip().strip("\"'")
        if stripped == "environment:":
            in_env = True
            env_indent = indent
            continue
        if in_env and indent <= (env_indent or 0):
            in_env = False
        if not in_env:
            continue
        item = stripped[2:].strip() if stripped.startswith("- ") else stripped
        key, sep, value = item.partition("=")
        if sep and key.strip() == "PORT":
            try:
                return int(value.strip().strip("\"'"))
            except ValueError:
                continue
        match = re.match(r"^PORT:\s*['\"]?(\d+)['\"]?$", item)
        if match:
            return int(match.group(1))
    return None


def _port_is_available(port):
    if port <= 0 or port > 65535:
        return False
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("0.0.0.0", port))
        return True
    except OSError:
        return False


def _reserved_host_ports(exclude_app_id=""):
    ports = {_runvard_port()}
    if not os.path.isdir(APPS_DIR):
        return ports

    for app_id in os.listdir(APPS_DIR):
        if app_id == exclude_app_id:
            continue
        path = _compose_file(app_id)
        try:
            with open(path) as f:
                ports.update(_host_ports_from_compose(f.read()))
        except OSError:
            continue
    return ports


def _next_available_host_port(preferred, reserved):
    port = preferred
    for _ in range(PORT_SEARCH_LIMIT):
        if port not in reserved and _port_is_available(port):
            return port
        port += 1
        if port > 65535:
            break
    return preferred


def _allocated_port_specs(app):
    reserved = _reserved_host_ports(exclude_app_id=app["id"])
    assigned = {}
    specs = []
    for spec in app["tpl"]["ports"]:
        preferred = _published_port(spec)
        if not preferred:
            specs.append(spec)
            continue

        if preferred in assigned:
            host_port = assigned[preferred]
        else:
            host_port = _next_available_host_port(preferred, reserved)
            assigned[preferred] = host_port
            reserved.add(host_port)
        specs.append(_replace_published_port(spec, host_port))
    return specs


def _allocated_env(app):
    t = app["tpl"]
    env = dict(t["env"])
    if t.get("network_mode") == "host" and "PORT" in env:
        reserved = _reserved_host_ports(exclude_app_id=app["id"])
        try:
            preferred = int(str(env["PORT"]))
        except (TypeError, ValueError):
            preferred = int(app.get("port") or 0)
        env["PORT"] = str(_next_available_host_port(preferred, reserved))
    return env


def _replace_environment_port(content, port):
    updated = re.sub(
        r"(^\s*-\s*PORT=)\d+(\s*$)",
        rf"\g<1>{port}\2",
        str(content or ""),
        count=1,
        flags=re.MULTILINE,
    )
    if updated != content:
        return updated
    updated = re.sub(
        r"(^\s*PORT:\s*['\"]?)\d+(['\"]?\s*$)",
        rf"\g<1>{port}\2",
        str(content or ""),
        count=1,
        flags=re.MULTILINE,
    )
    return updated


def _prepare_host_network_content(app, content):
    if app["tpl"].get("network_mode") != "host":
        return content
    port = _environment_port_from_compose(content) or int(app.get("port") or 0)
    reserved = _reserved_host_ports(exclude_app_id=app["id"])
    if port in reserved or not _port_is_available(port):
        port = _next_available_host_port(port, reserved)
        content = _replace_environment_port(content, port)
    return content


def _normalize_app_compose(app, content):
    """Apply catalog migrations to already saved app compose files."""
    if app.get("id") == "papervard":
        content = str(content or "").replace(
            "fetch('http://127.0.0.1:3000/login')",
            "fetch('http://'+process.env.HOSTNAME+':3000/login')",
        )
    return content


def _compose_uses_build(content):
    if yaml is not None:
        try:
            data = yaml.safe_load(content) or {}
        except Exception:
            data = {}
        services = data.get("services") if isinstance(data, dict) else {}
        if isinstance(services, dict):
            return any(isinstance(service, dict) and "build" in service
                       for service in services.values())
    return bool(re.search(r"^\s+build\s*:", str(content or ""), re.MULTILINE))


def _read_compose(app_id):
    try:
        with open(_compose_file(app_id)) as f:
            return f.read()
    except OSError:
        return ""


def _last_output_line(output):
    for line in reversed(output.splitlines()):
        line = line.strip()
        if line:
            return line[-220:]
    return ""


def _failure_step(label, output):
    detail = _last_output_line(output)
    return f"{label}: {detail}" if detail else label


def _compose_diagnostics(path):
    output = ""
    commands = (
        ["docker", "compose", "ps", "--all"],
        ["docker", "compose", "logs", "--no-color", "--tail", "50"],
    )
    for cmd in commands:
        try:
            result = subprocess.run(cmd, cwd=path, capture_output=True,
                                    text=True, timeout=30)
        except Exception:
            continue
        output += "\n" + result.stdout + result.stderr
    return output


def _fallback_down(app_id):
    """Remove app containers when docker compose down cannot parse the file."""
    output = ""
    names = {app_id}
    if app_id == "immich":
        names.update({"immich", "immich-server", "immich-machine-learning",
                      "immich-redis", "immich-postgres"})
    try:
        r = subprocess.run(
            ["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={app_id}"],
            capture_output=True, text=True, timeout=30
        )
        output += r.stdout + r.stderr
        names.update(x for x in r.stdout.splitlines() if x.strip())
    except Exception as e:
        output += f"\nFallback container lookup failed: {e}"
    hard_fail = False
    for name in sorted(names):
        try:
            r = subprocess.run(["docker", "rm", "-f", name],
                               capture_output=True, text=True, timeout=60)
            output += r.stdout + r.stderr
            if r.returncode != 0 and "No such container" not in r.stderr:
                hard_fail = True
        except Exception as e:
            output += f"\nFallback remove failed for {name}: {e}"
            hard_fail = True
    try:
        r = subprocess.run(["docker", "network", "rm", f"{app_id}_default"],
                           capture_output=True, text=True, timeout=30)
        output += r.stdout + r.stderr
    except Exception:
        pass
    return {"ok": not hard_fail, "output": output}


def _build_standard_compose(app):
    """
    Build a docker-compose.yml file from an app template.

    Args:
    -----
        app (dict):
            App catalog entry.

    Returns:
    --------
        str:
            Generated Docker Compose content.
    """
    t = app["tpl"]
    lines = ["services:", f"  {app['id']}:", f"    image: {t['image']}",
             "    restart: unless-stopped",
             f"    container_name: {app['id']}"]
    if t.get("build"):
        lines.append(f"    build: {t['build']}")
    if t.get("network_mode"):
        lines.append(f"    network_mode: {t['network_mode']}")
    if t["ports"]:
        lines.append("    ports:")
        for p in _allocated_port_specs(app):
            lines.append(f'      - "{p}"')
    if t["volumes"]:
        lines.append("    volumes:")
        for v in t["volumes"]:
            lines.append(f"      - {v}")
    env = _allocated_env(app)
    if env:
        lines.append("    environment:")
        for k, v in env.items():
            lines.append(f"      - {k}={v}")
    if t["extra"]:
        lines.append(t["extra"])
    return "\n".join(lines) + "\n"


def _allocated_named_ports(app, preferred_ports):
    reserved = _reserved_host_ports(exclude_app_id=app["id"])
    result = {}
    for name, preferred in preferred_ports.items():
        port = _next_available_host_port(preferred, reserved)
        result[name] = port
        reserved.add(port)
    return result


def _papervard_install_draft(app):
    ports = _allocated_named_ports(app, {"web": 3000, "onlyoffice": 8081})
    postgres_password = secrets.token_urlsafe(32)
    admin_password = secrets.token_urlsafe(32)
    onlyoffice_secret = secrets.token_urlsafe(32)
    compose = f"""name: papervard

services:
  papervard:
    image: ghcr.io/mschoettli/papervard:latest
    restart: unless-stopped
    depends_on:
      db:
        condition: service_healthy
      tika:
        condition: service_healthy
      onlyoffice:
        condition: service_healthy
    environment:
      DATABASE_URL: postgresql://papervard:{postgres_password}@db:5432/papervard?schema=public
      PAPERVARD_DATA_PATH: /data
      PAPERVARD_CONFIG_PATH: /config
      PAPERVARD_INTERNAL_URL: http://papervard:3000
      TIKA_URL: http://tika:9998
      ONLYOFFICE_INTERNAL_URL: http://onlyoffice
      ONLYOFFICE_JWT_SECRET: {onlyoffice_secret}
      NEXT_PUBLIC_ONLYOFFICE_URL: auto:{ports["onlyoffice"]}
      AUTH_COOKIE_SECURE: "false"
      OCR_LANGUAGES: deu+eng+fra+ita+spa
      TZ: Europe/Zurich
      AUTH_SECRET: ""
      PAPERVARD_SIGNING_SECRET: ""
      DICOM_FIELD_KEY: ""
      SEED_ADMIN_EMAIL: admin@papervard.local
      SEED_ADMIN_PASSWORD: {admin_password}
      PAPERVARD_UPDATE_MODE: external
      GITHUB_REPOSITORY: mschoettli/papervard
      GITHUB_BRANCH: main
    ports:
      - "{ports["web"]}:3000"
    volumes:
      - ./config:/config
      - ./data:/data
    command: ["sh", "-c", "./node_modules/.bin/prisma migrate deploy && npm run db:seed && node server.js"]
    healthcheck:
      test: ["CMD", "node", "-e", "fetch('http://'+process.env.HOSTNAME+':3000/login').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"]
      interval: 10s
      timeout: 5s
      retries: 30

  worker:
    image: ghcr.io/mschoettli/papervard:latest
    restart: unless-stopped
    depends_on:
      papervard:
        condition: service_healthy
    environment:
      DATABASE_URL: postgresql://papervard:{postgres_password}@db:5432/papervard?schema=public
      PAPERVARD_DATA_PATH: /data
      PAPERVARD_CONFIG_PATH: /config
      PAPERVARD_INTERNAL_URL: http://papervard:3000
      TIKA_URL: http://tika:9998
      ONLYOFFICE_INTERNAL_URL: http://onlyoffice
      ONLYOFFICE_JWT_SECRET: {onlyoffice_secret}
      AUTH_SECRET: ""
      PAPERVARD_SIGNING_SECRET: ""
      DICOM_FIELD_KEY: ""
      WORKER_IDLE_DELAY_MS: "1000"
      SMB_SYNC_ENABLED: "true"
      SMB_SYNC_INTERVAL_MS: "5000"
      TZ: Europe/Zurich
    volumes:
      - ./config:/config
      - ./data:/data
    command: ["npm", "run", "worker"]

  db:
    image: pgvector/pgvector:pg16
    restart: unless-stopped
    environment:
      POSTGRES_DB: papervard
      POSTGRES_USER: papervard
      POSTGRES_PASSWORD: {postgres_password}
      PGDATA: /papervard-data/postgres
    volumes:
      - ./data:/papervard-data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U papervard -d papervard"]
      interval: 5s
      timeout: 5s
      retries: 20

  tika:
    image: apache/tika:3.3.1.0-full
    restart: unless-stopped
    healthcheck:
      test: ["CMD-SHELL", "wget -q -O /dev/null http://127.0.0.1:9998/version || exit 1"]
      interval: 10s
      timeout: 5s
      retries: 20

  onlyoffice:
    image: onlyoffice/documentserver:9.4.0.1
    restart: unless-stopped
    environment:
      JWT_ENABLED: "true"
      JWT_SECRET: {onlyoffice_secret}
      JWT_HEADER: AuthorizationJwt
      ALLOW_PRIVATE_IP_ADDRESS: "true"
      TZ: Europe/Zurich
    ports:
      - "{ports["onlyoffice"]}:80"
    healthcheck:
      test: ["CMD-SHELL", "curl -fsS http://127.0.0.1/healthcheck || exit 1"]
      interval: 15s
      timeout: 10s
      retries: 30
      start_period: 60s
"""
    return compose, {
        "credentials": [
            {"label": "Admin email", "value": "admin@papervard.local"},
            {"label": "Initial admin password", "value": admin_password, "secret": True},
        ],
        "notes": [
            "Save the initial admin password before installing.",
            "SMB can be added later with Runvard's host share management.",
            "Back up both the config and data directories together.",
        ],
    }


def _app_install_draft(app):
    if app.get("compose_builder") == "papervard":
        return _papervard_install_draft(app)
    return _build_standard_compose(app), None


def build_compose(app):
    """Build a docker-compose.yml file from an app template."""
    return _app_install_draft(app)[0]


# ──────────────────────────────────────────────────────────────────────────
#  Katalog & Status
# ──────────────────────────────────────────────────────────────────────────

def _app_dir(app_id):
    return os.path.join(APPS_DIR, app_id)


def _compose_file(app_id):
    return os.path.join(_app_dir(app_id), "docker-compose.yml")


def is_installed(app_id):
    return os.path.isfile(_compose_file(app_id))


def _icon_url(app):
    icon = str(app.get("icon") or "")
    if icon.startswith(("http://", "https://", "/")):
        return icon
    return f"{ICON_BASE}/{icon}.svg"


def _running(app_id):
    """Return whether at least one app container is running."""
    path = _app_dir(app_id)
    if not os.path.isdir(path):
        return False
    try:
        r = subprocess.run(["docker", "compose", "ps", "--status", "running", "-q", app_id],
                           cwd=path, capture_output=True, text=True, timeout=15)
        if r.returncode != 0:
            r = subprocess.run(["docker", "compose", "ps", "--status", "running", "-q"],
                               cwd=path, capture_output=True, text=True,
                               timeout=15)
        return bool(r.stdout.strip())
    except Exception:
        return False


def _load_updates():
    try:
        with open(UPDATE_CACHE) as f:
            return json.load(f)
    except Exception:
        return {"checked": 0, "updates": [], "errors": {}}


def _is_compose_app_id(app_id):
    return str(app_id or "").startswith("compose:")


def _compose_project_from_app_id(app_id):
    project = str(app_id or "").split(":", 1)[1].strip()
    if not project:
        raise ValueError("Compose-Projekt nicht gefunden")
    return project


def _compose_app_entry(project, running=False, port=0, compose=""):
    return {
        "id": f"compose:{project}",
        "type": "compose",
        "project": project,
        "name": project,
        "icon": "⚙",
        "category": "Compose",
        "desc": "Custom Docker Compose project",
        "port": port,
        "first_party": False,
        "installed": True,
        "running": running,
        "update_available": False,
        "compose": compose,
    }


def get_catalog():
    """
    Return the full app catalog with live installation state.

    Returns:
    --------
        dict:
            Catalog categories and app entries.
    """
    updates = set(_load_updates().get("updates", []))
    out = []
    for app in CATALOG:
        installed = is_installed(app["id"])
        running = _running(app["id"]) if installed else False
        port = app["port"]
        if installed:
            port = _first_host_port_from_compose(_read_compose(app["id"]), port)
        out.append({
            "id": app["id"],
            "type": "app",
            "name": app["name"],
            "icon": _icon_url(app),
            "category": app["category"],
            "desc": app["desc"],
            "port": port,
            "first_party": bool(app.get("first_party")),
            "installed": installed,
            "running": running,
            "update_available": app["id"] in updates,
        })
    for project in docker_mgr.list_compose_projects():
        name = project["name"]
        compose = docker_mgr.get_compose(name).get("content", "")
        out.append(_compose_app_entry(
            name,
            running=project.get("running", False),
            port=project.get("port", 0),
            compose=compose,
        ))
    return {"categories": CATEGORIES, "apps": out}


def get_app(app_id):
    """
    Return one app entry and its Docker Compose content.

    Args:
    -----
        app_id (str):
            Catalog app ID.

    Returns:
    --------
        dict:
            App metadata and Compose content.

    Raises:
    -------
        ValueError:
            Raised when the app is unknown.
    """
    if _is_compose_app_id(app_id):
        project = _compose_project_from_app_id(app_id)
        projects = docker_mgr.list_compose_projects()
        status = next((p for p in projects if p.get("name") == project), None)
        if not status:
            raise ValueError("Compose-Projekt nicht gefunden")
        data = docker_mgr.get_compose(project)
        compose = data.get("content", "")
        port = _first_host_port_from_compose(compose, 0)
        return _compose_app_entry(project, running=status.get("running", False), port=port,
                                  compose=compose)

    app = next((a for a in CATALOG if a["id"] == app_id), None)
    if not app:
        raise ValueError("App nicht gefunden")
    installed = is_installed(app_id)
    if installed:
        compose = _read_compose(app_id)
        install_info = None
    else:
        compose, install_info = _app_install_draft(app)
    result = {
        "id": app["id"],
        "type": "app",
        "name": app["name"],
        "icon": _icon_url(app),
        "category": app["category"],
        "desc": app["desc"],
        "port": _first_host_port_from_compose(compose, app["port"]),
        "first_party": bool(app.get("first_party")),
        "installed": installed,
        "running": _running(app["id"]),
        "compose": compose,
    }
    if install_info:
        result["install_info"] = install_info
    return result


# ──────────────────────────────────────────────────────────────────────────
#  Installation & Aktionen
# ──────────────────────────────────────────────────────────────────────────

_install_jobs = {}


def install(app_id, content):
    """
    Start an app installation job in the background.

    Args:
    -----
        app_id (str):
            Catalog app ID.
        content (str):
            Docker Compose content to install.

    Returns:
    --------
        dict:
            Contains the installation job ID.

    Raises:
    -------
        ValueError:
            Raised when the app is unknown.
    """
    app = next((a for a in CATALOG if a["id"] == app_id), None)
    if not app:
        raise ValueError("App nicht gefunden")
    content = _normalize_app_compose(app, content)
    content = _prepare_host_network_content(app, content)
    if app.get("preflight_ports"):
        port_check = docker_mgr.check_compose_ports(app_id, content)
        if not port_check.get("ok"):
            ports = sorted({
                int(conflict["port"])
                for conflict in port_check.get("conflicts", [])
                if conflict.get("port") is not None
            })
            detail = ", ".join(str(port) for port in ports) or "unknown"
            raise ValueError(f"One or more Compose ports are already in use: {detail}")
    job_id = f"{app_id}_{int(time.time())}"
    _install_jobs[job_id] = {
        "status": "preparing", "app_id": app_id, "app_name": app["name"],
        "ok": None, "output": "", "step": "Vorbereitung…",
        "step_key": "preparing",
    }

    def run():
        path = _app_dir(app_id)
        os.makedirs(path, exist_ok=True)
        for directory in app.get("managed_directories", []):
            os.makedirs(os.path.join(path, directory), exist_ok=True)
        with open(_compose_file(app_id), "w") as f:
            f.write(content)
        job = _install_jobs[job_id]
        uses_build = _compose_uses_build(content)
        if not uses_build:
            job["status"] = "pulling"
            job["step"] = "Image wird heruntergeladen…"
            job["step_key"] = "pulling_image"
            try:
                r = subprocess.run(["docker", "compose", "pull"],
                                   cwd=path, capture_output=True, text=True, timeout=1800)
                job["output"] += r.stdout + r.stderr
                if r.returncode != 0:
                    job["status"] = "error"
                    job["ok"] = False
                    job["step"] = _failure_step("Image pull failed", job["output"])
                    job["step_key"] = "image_pull_failed"
                    return
            except subprocess.TimeoutExpired:
                job["status"] = "error"
                job["ok"] = False
                job["step"] = "Image pull timed out"
                job["step_key"] = "image_pull_timeout"
                return

        job["status"] = "starting"
        job["step"] = "Container wird gestartet…"
        job["step_key"] = "starting_container"
        try:
            up_cmd = ["docker", "compose", "up", "-d"]
            if uses_build:
                up_cmd.insert(3, "--build")
            r = subprocess.run(up_cmd,
                               cwd=path, capture_output=True, text=True,
                               timeout=int(app.get("install_timeout", 300)))
            job["output"] += r.stdout + r.stderr
            job["ok"] = r.returncode == 0
            job["status"] = "done" if r.returncode == 0 else "error"
            if r.returncode != 0:
                job["step"] = _failure_step("Container start failed",
                                            job["output"])
                job["step_key"] = "container_start_failed"
                return
            time.sleep(1)
            if not _running(app_id):
                job["ok"] = False
                job["status"] = "error"
                job["output"] += _compose_diagnostics(path)
                job["step"] = _failure_step("Container exited after start",
                                            job["output"])
                job["step_key"] = "container_exited"
                return
            job["step"] = "Fertig ✓"
            job["step_key"] = "done"
        except subprocess.TimeoutExpired:
            job["status"] = "error"
            job["ok"] = False
            job["step"] = "Container start timed out"
            job["step_key"] = "container_start_timeout"

    threading.Thread(target=run, daemon=True).start()
    return {"job_id": job_id}


def install_status(job_id):
    """
    Return the current status of an installation job.

    Args:
    -----
        job_id (str):
            Installation job ID.

    Returns:
    --------
        dict:
            Current job status and recent output.
    """
    job = _install_jobs.get(job_id)
    if not job:
        return {"status": "unknown"}
    return {
        "status": job["status"],
        "step": job["step"],
        "step_key": job.get("step_key", ""),
        "ok": job["ok"],
        "app_id": job["app_id"],
        "app_name": job.get("app_name", ""),
        "output": job.get("output", "")[-2000:],
    }


def save_compose(app_id, content):
    """Save an installed catalog app's docker-compose.yml without starting it."""
    if _is_compose_app_id(app_id):
        project = _compose_project_from_app_id(app_id)
        return docker_mgr.save_compose(project, content)
    app = next((a for a in CATALOG if a["id"] == app_id), None)
    if not app:
        raise ValueError("App nicht gefunden")
    path = _app_dir(app_id)
    os.makedirs(path, exist_ok=True)
    with open(_compose_file(app_id), "w") as f:
        f.write(content)
    return {"ok": True}


def action(app_id, act):
    """
    Execute an app lifecycle action.

    Args:
    -----
        app_id (str):
            Catalog app ID.
        act (str):
            One of start, stop, restart, down, or update.

    Returns:
    --------
        dict:
            Action result and recent command output.

    Raises:
    -------
        ValueError:
            Raised when the app is not installed or the action is unknown.
    """
    if _is_compose_app_id(app_id):
        project = _compose_project_from_app_id(app_id)
        compose_action = {
            "start": "up",
            "stop": "stop",
            "restart": "restart",
            "down": "down",
            "remove": "down",
        }.get(act)
        if not compose_action:
            raise ValueError("Unbekannte Aktion")
        return docker_mgr.compose_action(project, compose_action)

    path = _app_dir(app_id)
    if not os.path.isdir(path):
        raise ValueError("App nicht installiert")
    cmds = {
        "start":   [["docker", "compose", "up", "-d"]],
        "stop":    [["docker", "compose", "stop"]],          # pausieren
        "restart": [["docker", "compose", "restart"]],
        "down":    [["docker", "compose", "down"]],          # deinstallieren (Volumes bleiben)
        "update":  [["docker", "compose", "pull"],
                    ["docker", "compose", "up", "-d"]],
    }
    if act not in cmds:
        raise ValueError("Unbekannte Aktion")
    if act in ("start", "restart", "update"):
        app = next((a for a in CATALOG if a["id"] == app_id), None)
        if app:
            content = _read_compose(app_id)
            updated = _normalize_app_compose(app, content)
            updated = _prepare_host_network_content(app, updated)
            if updated != content:
                with open(_compose_file(app_id), "w") as f:
                    f.write(updated)
            if _compose_uses_build(updated):
                cmds["start"] = [["docker", "compose", "up", "--build", "-d"]]
                cmds["restart"] = [["docker", "compose", "up", "--build", "-d"]]
                cmds["update"] = [["docker", "compose", "build", "--pull"],
                                  ["docker", "compose", "up", "-d"]]
    output = ""
    ok = True
    for cmd in cmds[act]:
        r = subprocess.run(cmd, cwd=path, capture_output=True, text=True, timeout=600)
        output += r.stdout + r.stderr
        if r.returncode != 0:
            ok = False
            break
    if act == "down" and not ok:
        fallback = _fallback_down(app_id)
        output += "\nFallback uninstall:\n" + fallback["output"]
        ok = fallback["ok"]
    if act in ("start", "restart", "update") and ok:
        time.sleep(1)
        if not _running(app_id):
            ok = False
            output += "\nContainer is not running after the command."
            output += _compose_diagnostics(path)
    # Nach Update die Update-Markierung entfernen
    if act == "update" and ok:
        cache = _load_updates()
        cache["updates"] = [u for u in cache.get("updates", []) if u != app_id]
        try:
            with open(UPDATE_CACHE, "w") as f:
                json.dump(cache, f)
        except Exception:
            pass
    # Nach Down die Compose-Datei entfernen (Daten bleiben)
    if act == "down" and ok:
        try:
            os.remove(_compose_file(app_id))
        except OSError:
            pass
    return {"ok": ok, "output": output[-2000:]}


# ──────────────────────────────────────────────────────────────────────────
#  Update-Prüfung (alle 12h im Hintergrund)
# ──────────────────────────────────────────────────────────────────────────

def _check_image_update(path):
    """Compare local image IDs with published manifest config digests."""
    result = subprocess.run(
        ["docker", "compose", "config", "--images"],
        cwd=path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Could not read Compose images")

    # Multiple services may use the same image (for example web and worker).
    images = list(dict.fromkeys(
        line.strip() for line in result.stdout.splitlines() if line.strip()
    ))
    for image in images:
        local = subprocess.run(
            ["docker", "image", "inspect", image, "--format", "{{json .Id}}"],
            cwd=path,
            capture_output=True,
            text=True,
            timeout=30,
        )
        remote = subprocess.run(
            ["docker", "manifest", "inspect", "--verbose", image],
            cwd=path,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if local.returncode != 0:
            raise RuntimeError(local.stderr.strip() or f"Local image unavailable: {image}")
        if remote.returncode != 0:
            raise RuntimeError(remote.stderr.strip() or f"Registry manifest unavailable: {image}")

        local_digest = json.loads(local.stdout)
        manifest_data = json.loads(remote.stdout)
        manifests = manifest_data if isinstance(manifest_data, list) else [manifest_data]
        remote_digests = set()
        for entry in manifests:
            descriptor = entry.get("Descriptor") or {}
            platform = descriptor.get("platform") or {}
            if platform.get("os") == "unknown":
                continue
            manifest = (
                entry.get("OCIManifest")
                or entry.get("SchemaV2Manifest")
                or entry
            )
            digest = (manifest.get("config") or {}).get("digest")
            if digest:
                remote_digests.add(digest)
        if not local_digest or not remote_digests:
            raise RuntimeError(f"Image digest unavailable: {image}")
        if local_digest not in remote_digests:
            return True
    return False


def check_updates(force=False):
    """Prüft alle installierten Apps auf Image-Updates. Cached 12h."""
    cache = _load_updates()
    age = time.time() - cache.get("checked", 0)
    if not force and age < UPDATE_INTERVAL:
        return cache
    updates = []
    errors = {}
    if os.path.isdir(APPS_DIR):
        for app_id in os.listdir(APPS_DIR):
            path = _app_dir(app_id)
            if os.path.isfile(os.path.join(path, "docker-compose.yml")):
                try:
                    if _check_image_update(path):
                        updates.append(app_id)
                except Exception as exc:
                    errors[app_id] = str(exc)
    cache = {"checked": time.time(), "updates": updates, "errors": errors}
    try:
        os.makedirs(APPS_DIR, exist_ok=True)
        with open(UPDATE_CACHE, "w") as f:
            json.dump(cache, f)
    except Exception:
        pass
    return cache


def start_update_checker():
    """Startet einen Hintergrund-Thread der alle 12h prüft."""
    def loop():
        # Erste Prüfung 60s nach Start (damit Boot nicht blockiert)
        time.sleep(60)
        while True:
            try:
                check_updates(force=True)
            except Exception:
                pass
            time.sleep(UPDATE_INTERVAL)
    t = threading.Thread(target=loop, daemon=True)
    t.start()
