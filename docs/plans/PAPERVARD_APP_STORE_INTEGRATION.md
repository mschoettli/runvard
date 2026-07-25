# Papervard App-Store Integration

## Understanding

- Papervard soll als kuratierte Runvard-App mit einer sicheren Ein-Klick-Installation verfügbar sein.
- Zielgruppe sind Familien und kleine Teams, die Papervard lokal auf einem Runvard-Server betreiben.
- Runvard bleibt Eigentümer des Container-Lebenszyklus und der Image-Updates.
- Papervard-Dokumente und wiederherstellungsrelevante Schlüssel müssen persistent und gemeinsam sicherbar sein.
- Pflichtgeheimnisse dürfen nicht mit bekannten Standardwerten ausgeliefert werden.
- Die Installation muss Portkonflikte vermeiden und darf Runvards Host-Samba nicht auf Port 445 verdrängen.
- Nicht Ziel ist eine allgemeine Neuentwicklung des Runvard-App-Katalogformats für beliebige komplexe Apps.

## Annahmen und Betriebsziele

- Eine Papervard-Installation läuft als einzelner Compose-Stack auf einem Runvard-Host.
- Die primären Plattformen sind `linux/amd64` und `linux/arm64`.
- Webzugriff und ONLYOFFICE werden im LAN veröffentlicht; HTTPS bleibt Aufgabe eines vorgeschalteten Reverse-Proxys.
- Runvard verwaltet Updates. Papervards eingebauter Watchtower-Trigger wird in diesem Modus deaktiviert.
- Papervards SMB-Arbeitsablage wird nicht über einen zweiten Samba-Container auf Port 445 veröffentlicht. Sie kann später bewusst über Runvards Host-Freigaben angebunden werden.
- `config` und `data` liegen unter Runvards App-Datenpfad oder auf einem im Installationsdialog gewählten Datenträger.
- Für sensible DICOM-Daten bleibt ein verschlüsselter Host-Datenträger die Verantwortung des Administrators.

## Gewählter Ansatz

Runvard erhält einen Papervard-spezifischen Compose-Builder innerhalb des bestehenden kuratierten Katalogs. Der Builder erzeugt bei jedem neuen Installationsentwurf unabhängige sichere Geheimnisse, verwendet ausschließlich veröffentlichte Images und liefert Installationshinweise sowie die einmalig zu sichernden Startzugangsdaten an die Oberfläche.

Der aktive Stack enthält Papervard, Worker, PostgreSQL, Apache Tika und ONLYOFFICE. Persistenz erfolgt über direkte Bind-Mounts für `config` und `data`. Runvard prüft und vergibt die veröffentlichten Web- und ONLYOFFICE-Ports. Ein zusätzlicher Samba-Container ist bewusst nicht Teil der Standardinstallation.

Papervard erhält einen extern verwalteten Update-Modus. In diesem Modus bleibt die Versionsanzeige verfügbar, der interne Watchtower-Start wird jedoch nicht angeboten. Die Image-Pipeline veröffentlicht Images für AMD64 und ARM64.

## Verworfene Alternativen

1. **Das bestehende Papervard-Compose unverändert übernehmen.** Verworfen, weil `build: .`, Pflichtvariablen, relative Local-Driver-Mounts, Watchtower und Port 445 keine zuverlässige Store-Installation ergeben.
2. **Watchtower zusammen mit Runvard betreiben.** Verworfen, weil zwei Update-Eigentümer denselben Stack verändern würden und dafür der Docker-Socket an einen zusätzlichen Container gereicht werden müsste.
3. **Papervards Samba-Container standardmäßig starten.** Verworfen, weil Runvard selbst Host-Samba verwaltet und Port 445 nicht sinnvoll auf einen alternativen SMB-Port verschoben werden kann.

## Fehlerbehandlung und Sicherheit

- Der Backend-Installer prüft alle veröffentlichten Ports unmittelbar vor dem Schreiben und Starten des Compose-Stacks.
- Für Papervard werden sichere Zufallswerte serverseitig erzeugt; es gibt keine bekannten Standardpasswörter.
- Zugangsdaten werden nur für einen noch nicht installierten Entwurf zurückgegeben und anschließend im gespeicherten Compose-Dokument weiterverwaltet.
- Der Installer legt Bind-Mount-Verzeichnisse an, bevor Compose gestartet wird.
- Eine Deinstallation entfernt Container und Netzwerk, lässt `config` und `data` jedoch bestehen.

## Teststrategie

- Katalogtest für Metadaten und lokales Icon.
- Compose-Test für Services, Image-only-Betrieb, Persistenz, sichere Geheimnisse und fehlenden Watchtower/Samba-Dienst.
- Porttest für automatische Vergabe und Konflikterkennung aller aktiven Papervard-Ports.
- Installertest für das Anlegen persistenter Verzeichnisse.
- Papervard-Test für extern verwaltete Updates.
- Compose-Validierung sowie Papervard-Typcheck, Tests und Build.

## Entscheidungsprotokoll

| Entscheidung | Begründung |
|---|---|
| Spezifischer Builder statt allgemeinem Katalog-Umbau | Begrenzter Umfang und geringeres Regressionsrisiko |
| Direkte Bind-Mounts | Runvard kann Speicherorte auswählen und Verzeichnisse zuverlässig anlegen |
| Runvard besitzt Updates | Ein eindeutiger Lebenszyklus-Eigentümer und kein zusätzlicher Docker-Socket |
| SMB standardmäßig nicht als Container | Vermeidet Konflikt mit Runvards Host-Samba auf Port 445 |
| Serverseitig erzeugte Installationsgeheimnisse | Keine unsicheren Defaults und kein Vertrauen in Client-Zufall |
| Multi-Arch-Images | Entspricht Papervards dokumentierter AMD64-/ARM64-Unterstützung |
