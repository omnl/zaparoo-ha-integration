# KidsStation v0.1 einrichten

Diese erste Version verwaltet **ein gemeinsames Tageslimit für alle Medien** pro Kind. Du kannst für jeden Wochentag einen anderen Wert setzen, bestehende HA-Personen verknüpfen und Bonuszeit für heute vergeben. Der Wochenplan läuft auf Batocera, auch wenn Home Assistant ausfällt.

## Voraussetzungen

- Batocera mit laufendem Zaparoo Core. Core muss `profiles`, `profiles.active`, `profiles.update`, `clients.current` und `before_media_start` unterstützen. Mit älteren Core-Versionen ohne diese Funktionen meldet der Agent einen Synchronisierungsfehler.
- Die Kinderprofile sind bereits in Zaparoo angelegt; die NFC-Profilkarten bleiben die Anmeldung am Gerät.
- Home Assistant ab 2025.2, mit vorhandenen `person.*`-Entitäten.
- Home Assistant und Batocera verwenden dieselbe Zeitzone, beispielsweise `Europe/Zurich`. Core zählt seine Kalendertage nach der Gerätezeit; KidsStation verwendet die HA-Zeitzone für den Wochenplan.

## 1. Agent auf Batocera installieren

Entpacke das Installationspaket auf Batocera, zum Beispiel nach `/userdata/system/kidsstation-release`. Führe dort aus:

```sh
sh batocera/install.sh
```

Der Installer wählt die passende mitgelieferte Linux-Binärdatei für x86_64 oder ARM64. Er speichert die Konfiguration unter `/userdata/system/kidsstation/config.json`. Ein Update erhält Konfiguration, Token, Wochenplan und Bonuszeit.

Lies den Wert `token` aus dieser Datei ab; er wird später in Home Assistant benötigt. Port `7498` ist ausschliesslich für Home Assistant im Heimnetz vorgesehen. Der bestehende HA-Fernzugriff dient weiterhin zur Bedienung von unterwegs.

## 2. Start-Hook in Zaparoo ergänzen

Ergänze die folgenden Schlüssel in den **bereits vorhandenen Abschnitten** von Zaparoo `config.toml`:

```toml
[profiles]
require_for_launch = true

[launchers]
before_media_start = "**http.get:http://127.0.0.1:7499/check-launch"

[zapscript]
allow_http = ['^http://127[.]0[.]0[.]1:7499/check-launch$']

[playtime.limits]
warnings = ["10m", "5m", "1m"]
```

Behalte vorhandene Einträge in `allow_http` bei und ergänze die neue URL. Wenn bereits ein `before_media_start`-Hook existiert, verkette ihn mit `||` und dem KidsStation-Aufruf. Starte Zaparoo anschliessend neu.

Dieser Hook gehört zur Installation: **0 Minuten** sperren so einen ganzen Tag. Zaparoo interpretiert sein eigenes Limit `0` als unbegrenzt. Ist der Agent noch nicht bereit, schlägt der Hook fehl und verhindert neue Starts. Bereits laufende Medien unterliegen weiterhin dem zuletzt von Zaparoo übernommenen Limit.

`require_for_launch` verhindert Starts ohne persönliche Profilkarte. Erwachsene können eigene Profile ohne KidsStation-Wochenplan behalten.

## 3. HA-Integration installieren

1. Kopiere `custom_components/zaparoo` nach `<HA-Konfiguration>/custom_components/zaparoo`. Dieser Fork ersetzt die vorhandene Zaparoo-Integration mit derselben Domain.
2. Starte Home Assistant neu.
3. Unter **Einstellungen → Geräte & Dienste → Integration hinzufügen** wählst du **KidsStation (Zaparoo)**.
4. Wähle **Batocera-Agent**, die IP von Batocera, Port `7498` und den Token aus `config.json`.

Die Verbindung wird vor dem Speichern geprüft. Ein offener Socket allein genügt nicht; Profile, Medien, Spielzeit und Berechtigungen müssen geladen sein.

Bestehende direkte Zaparoo-Einträge funktionieren weiterhin für Medien, Profile und Ereignisse. Für KidsStation-Wochenpläne lege einen Agent-Eintrag an; entferne den bisherigen direkten Eintrag anschliessend, um doppelte Geräte zu vermeiden. Die direkte Verbindung implementiert kein Zaparoo-Pairing und ist deshalb nur für kompatibel konfigurierte Core-Verbindungen vorgesehen.

## 4. Jedes Kind konfigurieren

Öffne **Konfigurieren** an der Integration, wähle das Zaparoo-Profil und verknüpfe die entsprechende `person.*`-Entität. Lege die erlaubten Minuten für Montag bis Sonntag fest.

- `0` bedeutet: an diesem Tag gesperrt.
- Bonus `+15`, `+30` oder `+60` erhöht das gemeinsame Tageslimit.
- Bonus läuft um Mitternacht der konfigurierten Zeitzone ab.
- Das Deaktivieren des Wochenplans stellt die vorherigen Zaparoo-Limitüberschreibungen wieder her, einschliesslich eines vorhandenen Session-Limits.
- Anwesenheit oder Änderungen an HA-Personen wechseln das aktive Zaparoo-Profil nicht.

Für jedes Profil entstehen ein Gerät, Tageslimit- und Bonussensoren sowie drei Bonusknöpfe. Spielzeit und Restzeit sind Gerätesensoren aus dem aktuellen Core-Playtime-Zustand. Sie werden nicht nachträglich einer HA-Person zugerechnet, weil Core bei einem Profilwechsel die laufende Spielzeit weiterhin dem Startprofil zuordnet.

## 5. Warnmeldungen

Für Kodi muss **Steuerung über HTTP erlauben** aktiv sein. Trage bei Bedarf Benutzername und Passwort in `config.json` ein. Der Agent sendet `GUI.ShowNotification` an Kodi.

Für RetroArch ergänze in `/userdata/system/batocera.conf`:

```ini
global.retroarch.network_cmd_enable=true
global.retroarch.network_cmd_port=55355
```

Starte danach ein neues RetroArch-Spiel. EmulationStation erhält Text über seine lokale `/notify`-Schnittstelle auf Port `1234`. Standalone-Emulatoren wie Dolphin haben in v0.1 kein eigenes Bildschirm-Overlay; ES-Meldungen werden dort möglicherweise erst sichtbar, wenn ES wieder im Vordergrund ist.

Bonuszeit erhöht das Tageslimit. Ein separat in Core konfiguriertes Session-Limit bleibt bestehen.

## Aktionen und Dashboard

Die Domain bleibt `zaparoo`. In HA-Automationen sind verfügbar:

```yaml
action: zaparoo.add_time
data:
  device_id: DEINE_KIDSSTATION_DEVICE_ID
  person: person.dein_kind
  minutes: 30
```

Alternativ kannst du `profile_id` statt `person` verwenden. Eine Person muss auf diesem Gerät genau einem Profil zugeordnet sein. Ein vollständiger Wochenplan wird mit `zaparoo.set_schedule` und `minutes: [60, 60, 90, 60, 90, 120, 120]` gesetzt; die Reihenfolge ist Montag bis Sonntag. Weitere Aktionen: `launch`, `stop`, `media` und `switch_profile`.

Füge die erzeugten Sensoren und Knöpfe zu einem normalen HA-Dashboard hinzu. Ein Beispiel steht unter `examples/dashboard.yaml`; die Entity-IDs müssen durch die bei dir erzeugten IDs ersetzt werden.

## Prüfen und Fehler finden

```sh
batocera-services status kidsstation
tail -n 50 /userdata/system/kidsstation/agent.log
curl -i http://127.0.0.1:7499/check-launch
```

Der letzte Aufruf liefert `200` für einen grundsätzlich erlaubten Start, `403` bei einem Null-Minuten-Tag und `503` während einer fehlenden Synchronisierung. Das tatsächliche Verbrauchslimit prüft Zaparoo selbst.

Zum Testen zuerst ein Kinderprofil mit einem kleinen Tageslimit verwenden. Ein echter End-to-End-Test auf deiner Batocera-Hardware und deiner HA-Installation ist noch nötig; die automatisierten Tests verwenden echte WebSockets mit einem simulierten Core.
