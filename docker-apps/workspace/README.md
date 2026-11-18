# Workspace in Runvard

Runvard verwaltet diesen Compose-Stack als feste First-Party-App. Browserwerte
werden weder als Compose-Datei noch als Image-, Pfad- oder Kommandoquelle
akzeptiert.

## Lokaler Start

Der Startpfad akzeptiert ausschließlich die bereits lokal vorhandenen Images
`workspace-web:local` und `workspace-migrator:local`. Er prüft beide Images,
startet PostgreSQL, führt die Migration aus und startet danach Web und Gateway.
Der Gateway bindet sicher voreingestellt an `127.0.0.1:3100`. Für einen
ausdrücklich freigegebenen Zugriff aus dem lokalen Netzwerk enthält die lokale
Datei `/opt/runvard/data/apps/workspace/bind-address` ausschließlich die
private LAN-Adresse des Runvard-Servers, zum Beispiel `192.168.178.60`.
Öffentliche und fehlerhafte IP-Adressen lehnt Runvard fail-closed ab; andere
Interfaces werden nicht automatisch freigegeben.

Die Images werden im Nushira-Checkout lokal gebaut:

```sh
docker compose -f compose.workspace.yaml build web migrate
```

Synthetische Planungsdaten werden dort mit dem vorhandenen Bootstrap-Profil
erzeugt. Dieser lokale Bootstrap ist keine Releasepromotion.

## Signaturverifizierte Updates

Vor einem Update müssen unter `/opt/runvard/data/apps/workspace` folgende
lokal bereitgestellten, kanonischen Dateien vorliegen:

- `release-candidate.json` und `release-candidate.json.sha256`
- `release-candidate.sig`
- `release-promotion.json` und `release-promotion.json.sha256`
- `trust-root/trust-root.json` und der referenzierte Public Key unter
  `trust-root/keys/`

Cosign muss als reguläre, nicht verlinkte ausführbare Datei unter
`/opt/runvard/bin/cosign` installiert sein. Private Schlüssel und Passphrasen
gehören niemals nach Runvard. Der Updater prüft geschlossenes Schema,
Kandidaten- und Promotionshash, alle Cross-Bindungen, aktuellen aktiven und
nicht widerrufenen Trust-Key, dessen DER/SPKI-Fingerprint und anschließend die
detached Signatur mit `cosign verify-blob`.

Fehlt eine Evidenz oder schlägt eine Prüfung fehl, bleibt der Pfad geschlossen.
Dieser lokale Katalogpfad führt auch nach der Verifikation keinen Registry-Pull
aus: Beide digestgebundenen Images müssen bereits lokal vorliegen. Vor dem
Zustand `verified` erfolgen weder Imagezugriff noch Backup, Migration oder
Laufzeitwechsel.
