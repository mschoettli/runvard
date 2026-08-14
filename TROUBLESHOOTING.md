# Probleme und Lösungen

Diese Seite sammelt bekannte Installations- und Updateprobleme von runvard sowie
die dazugehörigen, überprüfbaren Lösungen.

## Das verifizierte Update verlangt eine GitHub-Anmeldung

### Symptome

Das Update endet mit `failed` beziehungsweise Exit-Code `4`. In
`/opt/runvard/data/runvard-update.log` steht:

```text
To get started with GitHub CLI, please run: gh auth login
Alternatively, populate the GH_TOKEN environment variable with a GitHub API authentication token.
```

Bei einer veralteten GitHub CLI kann stattdessen die allgemeine Befehlsübersicht
erscheinen, ohne dass der Unterbefehl `attestation` aufgeführt wird.

### Ursache

runvard installiert nur verifizierte Release-Archive. Neben der SHA-256-Prüfsumme
wird die GitHub-Artefaktattestierung mit `gh attestation verify` geprüft. Dafür
benötigt der als `root` laufende Updateprozess:

- eine aktuelle GitHub CLI mit dem Unterbefehl `attestation`,
- eine gültige GitHub-Anmeldung für `root`,
- und Zugriff auf deren Konfiguration unter `/root/.config/gh`.

### Lösung

Zuerst prüfen, ob die GitHub CLI aktuell genug ist:

```bash
gh --version
gh attestation --help
```

Fehlt `attestation`, die aktuelle GitHub CLI gemäß der
[offiziellen Linux-Installationsanleitung](https://github.com/cli/cli/blob/trunk/docs/install_linux.md)
installieren.

Auf einem Server ohne grafischen Browser die Anmeldung so starten:

```bash
BROWSER=true gh auth login --hostname github.com --git-protocol https --web
```

Den angezeigten Einmalcode nicht veröffentlichen. Auf einem vertrauenswürdigen
anderen Gerät [github.com/login/device](https://github.com/login/device) öffnen,
den Code dort eingeben und warten, bis das Server-Terminal die erfolgreiche
Anmeldung bestätigt.

Anschließend Konfiguration und Anmeldung prüfen:

```bash
chmod 700 /root/.config/gh
chmod 600 /root/.config/gh/hosts.yml
GH_CONFIG_DIR=/root/.config/gh gh auth status
```

Aktuelle runvard-Versionen übergeben dieses Konfigurationsverzeichnis automatisch
an den systemd-Updater. Falls noch eine ältere Version installiert ist, kann das
erste korrigierte Update einmal über denselben verifizierten Pfad gestartet werden:

```bash
GH_CONFIG_DIR=/root/.config/gh bash /opt/runvard/install.sh --verified-release --yes
```

Danach funktionieren weitere Updates wieder über die runvard-Oberfläche.

### Diagnose

Bei einem erneuten Fehler liefern diese Befehle den relevanten Status und das
Updateprotokoll:

```bash
cat /opt/runvard/data/runvard-update.status.json
tail -n 100 /opt/runvard/data/runvard-update.log
```

Passwörter, Zugriffstoken, Einmalcodes und private Schlüssel dürfen nicht in
Fehlerberichte oder Screenshots übernommen werden.
