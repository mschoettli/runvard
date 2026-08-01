# Geführter Backup-Assistent

## Status

Erste Ausbaustufe implementiert am 1. August 2026.

Enthalten sind der responsive Vier-Schritt-Assistent, automatisch erkannte
Quellen und Mountpoints, ein serverseitiger Ordnerbrowser, Vorabprüfungen,
Adminschutz und die verständliche Kennzeichnung der bestehenden Spiegelung.
Neue SMB- oder NFS-Verbindungen werden weiterhin über den Dateimanager
eingerichtet. Versionierte Backups bleiben eine spätere Ausbaustufe.

## Verständnis

- Der bisherige Dialog verlangt Quell- und Zielpfade als Freitext.
- Nutzer sollen keine Linux-Pfade kennen müssen, um ein wirksames Backup einzurichten.
- Runvard soll alle sinnvollen lokalen, externen und eingebundenen Speicherorte auffindbar machen.
- Die primäre Zielgruppe sind Homelab-Nutzer mit unterschiedlicher technischer Erfahrung.
- Der Standardablauf soll einfach, visuell und mit grossen Kacheln geführt sein.
- Manuelle Pfade und technische Details bleiben in einem Expertenmodus verfügbar.
- Fehlkonfigurationen und vermeintliche Backups auf demselben Datenträger müssen verhindert oder deutlich erklärt werden.

## Annahmen

- Runvard kann Verzeichnisse, Datenträger, Mountpoints und deren freien Speicher serverseitig ermitteln.
- Bereits eingebundene SMB- und NFS-Freigaben können wie andere Speicherziele angezeigt werden.
- Neue Netzwerkspeicher werden über einen separaten Verbindungsablauf eingerichtet und danach im Assistenten angeboten.
- Die Oberfläche muss auch bei vielen Verzeichnissen schnell bleiben; Verzeichnisinhalte werden deshalb erst beim Öffnen geladen.
- Nur berechtigte Administratoren dürfen systemweite Backup-Jobs und neue Speicherverbindungen anlegen.
- Die erste Ausbaustufe darf bestehende Spiegelungsfunktionen verwenden, muss deren Löschverhalten aber transparent benennen.

## Ziele

1. Ein Backup ohne manuelle Pfade in höchstens vier verständlichen Schritten einrichten.
2. Quelle, Ziel, Schutzwirkung und Zeitplan vor dem Speichern eindeutig erklären.
3. Fehler früh erkennen, bevor ein Job gespeichert oder ausgeführt wird.
4. Fortgeschrittenen Nutzern weiterhin vollständige Kontrolle ermöglichen.

## Nicht-Ziele der ersten Ausbaustufe

- Automatisches Erkennen oder Anlegen beliebiger Cloud-Konten.
- Verstecken aller technischen Informationen vor erfahrenen Nutzern.
- Behaupten, eine Spiegelung mit `rsync --delete` sei ein versioniertes Backup.
- Vollständige Wiederherstellungs- und Desaster-Recovery-Automatisierung.

## Ablauf

### Schritt 1: Daten auswählen

Der Assistent zeigt erkannte Quellen als auswählbare Kacheln:

- Dokumente
- Fotos
- Medien
- Docker-Daten
- Benutzerverzeichnisse
- Ordner auswählen

Mehrere Quellen dürfen ausgewählt werden. Jede Kachel zeigt Name, geschätzte Grösse und optional den tatsächlichen Pfad unter „Details“. „Ordner auswählen“ öffnet einen vollständigen serverseitigen Verzeichnisbrowser.

### Schritt 2: Ziel auswählen

Verfügbare Ziele werden als Kacheln gruppiert:

- Empfohlene Ziele
- USB- und externe Laufwerke
- NAS und eingebundene Netzwerkfreigaben
- Interne Datenträger
- Andere Runvard-Server, sobald unterstützt
- Neuen Speicher verbinden

Eine Zielkachel zeigt Anzeigename, Typ, Online-Status, freien Speicher, Schreibbarkeit und Schutzbewertung. Ein Ziel auf einem anderen physischen Datenträger wird bevorzugt. Ein Ziel auf demselben Datenträger bleibt sichtbar, erhält aber die Warnung „Nicht vor einem Laufwerksausfall geschützt“.

### Schritt 3: Zeitplan auswählen

Die primären Optionen sind:

- Automatisch täglich – empfohlen
- Automatisch wöchentlich
- Nur wenn ich es starte

Uhrzeit, Aufbewahrung, Ausschlüsse und erweiterte Optionen liegen unter „Weitere Einstellungen“.

### Schritt 4: Prüfen und einrichten

Runvard fasst den Job in Alltagssprache zusammen, zum Beispiel:

> Deine Dokumente und Fotos werden jeden Tag auf „Synology NAS“ gesichert. Gelöschte Dateien bleiben 30 Tage wiederherstellbar.

Der Text muss der tatsächlichen Technik entsprechen. Wenn nur eine Spiegelung verfügbar ist, lautet die Aussage stattdessen:

> Das Ziel wird an den aktuellen Stand der Quelle angeglichen. Dateien, die in der Quelle gelöscht wurden, können auch am Ziel gelöscht werden.

Die primäre Aktion heisst „Backup einrichten“. Danach kann die erste Sicherung sofort gestartet werden.

## Verzeichnisbrowser

Der Verzeichnisbrowser bietet:

- Breadcrumb-Navigation
- Suche nach Ordnernamen
- Eine Ebene höher
- Neue Ordner anlegen, sofern zulässig
- Zuletzt verwendete Orte
- Darstellung von Mountpoint, Grösse und Berechtigungen
- „Alle Ordner anzeigen“ für versteckte und technische Bereiche
- Manuelle Pfadeingabe im Expertenmodus

Virtuelle oder gefährliche Systembereiche wie `/proc`, `/sys` und `/dev` werden standardmässig gesperrt. Andere sensible Bereiche benötigen eine bewusste Bestätigung.

## Visuelle Gestaltung

- Dialogbreite ungefähr 800 Pixel auf Desktop, auf kleinen Geräten bildschirmfüllend.
- Fortschrittsanzeige: Daten → Ziel → Zeitplan → Abschluss.
- Grosse Kacheln mit Symbol, verständlichem Namen und höchstens zwei Zusatzzeilen.
- Auswahlzustand durch Rahmen, Hintergrund und Häkchen; Farbe ist nie der einzige Indikator.
- Technische Pfade sind sekundär und auf Wunsch sichtbar.
- Navigation bleibt unten an stabiler Position: „Zurück“ und „Weiter“.
- Fokusführung, Tastaturbedienung und ausreichend grosse Klickflächen sind verpflichtend.

## Vorabprüfungen

Vor dem Speichern und erneut vor jedem Lauf wird geprüft:

- Quelle existiert und ist lesbar.
- Ziel existiert und ist beschreibbar.
- Quelle und Ziel sind weder identisch noch ineinander verschachtelt.
- Das Ziel hat voraussichtlich genügend freien Speicher.
- Ein Netzwerkziel ist erreichbar.
- Der physische Datenträger von Quelle und Ziel ist bekannt.
- Ausschlüsse verhindern rekursive Sicherungen von Zielinhalten.
- Bei einer Spiegelung wurde das Löschverhalten ausdrücklich angezeigt.

Blockierende Fehler erscheinen direkt an der betroffenen Kachel. Technische Fehler werden in verständliche Handlungsanweisungen übersetzt; Details bleiben aufklappbar.

## Sicherheits- und Zuverlässigkeitsregeln

- Nur bestätigte Administratoren dürfen Jobs erstellen, ändern oder starten.
- Pfade werden serverseitig kanonisiert und validiert; Clientwerte werden nicht vertraut.
- Symlinks, Mount-Wechsel und Pfad-Traversal werden bei der Validierung berücksichtigt.
- Zugangsdaten für Netzwerkspeicher erscheinen nie in Pfaden, Logs oder Fehlermeldungen.
- Der erste Lauf kann optional als Vorschau die erwarteten Änderungen und Löschungen zeigen.
- Versioniertes Backup, Spiegelung und Archivkopie sind getrennte Modi mit korrekter Bezeichnung.
- Ein erfolgreicher Prozessstart gilt nicht allein als erfolgreiches Backup; Ergebnis, Dauer und übertragene Daten werden protokolliert.

## Leistung und Skalierung

- Verzeichnislisten werden bei Bedarf geladen und paginiert oder virtualisiert.
- Grössenberechnungen laufen asynchron und blockieren die Auswahl nicht.
- Mounts und Speicherinformationen dürfen kurzzeitig zwischengespeichert werden.
- Offline-Ziele bleiben sichtbar, verursachen aber keine langen Wartezeiten im Dialog.
- Das Design soll mindestens einige Dutzend Mounts und grosse Verzeichnisbäume bedienen können.

## Tests und Abnahmekriterien

- Ein Nutzer kann ohne Pfadeingabe ein Backup auf ein USB-Laufwerk einrichten.
- Ein eingebundenes NAS ist als Ziel mit Status und freiem Speicher auswählbar.
- Dasselbe Quell- und Zielverzeichnis wird blockiert.
- Ein Ziel innerhalb der Quelle wird blockiert.
- Ein schreibgeschütztes oder nicht erreichbares Ziel zeigt eine verständliche Meldung.
- Ein Ziel auf demselben Datenträger zeigt die Schutzwarnung.
- Tastatur- und Screenreader-Nutzung funktionieren in allen vier Schritten.
- Der Abschlussdialog beschreibt Spiegelung und Versionierung korrekt.
- Manuelle Pfade bleiben im Expertenmodus verfügbar und werden serverseitig validiert.

## Entscheidungsliste

1. **Geführter Vier-Schritt-Assistent statt Freitextformular.** Dadurch wird die Aufgabe und nicht die Serverstruktur zum Mittelpunkt.
2. **Kacheln als primäres Auswahlmuster.** Sie verbinden verständliche Namen, Status und Empfehlungen in einer grossen Klickfläche.
3. **Vollständiger Browser hinter „Ordner auswählen“.** Einsteiger erhalten schnelle Vorgaben, erfahrene Nutzer verlieren keine Kontrolle.
4. **Technische Pfade sekundär anzeigen.** Pfade bleiben nachvollziehbar, dominieren aber nicht den Ablauf.
5. **Speicherziele zentral verwalten und im Assistenten wiederverwenden.** Dies trennt Verbindungsdaten von einzelnen Backup-Jobs.
6. **Ungeeignete Ziele erklären statt pauschal verstecken.** Nutzer verstehen dadurch die tatsächliche Schutzwirkung.
7. **Spiegelung und versioniertes Backup sprachlich und technisch trennen.** Dies verhindert ein falsches Sicherheitsversprechen.
