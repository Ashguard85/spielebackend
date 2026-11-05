# ZIP-Vorschau / Debug – Spiele-Zentrale v2.5.0

Im Adminbereich gibt es neu **ZIP Vorschau / Debug**.

## Ablauf

1. Administrator-Token eingeben.
2. Unter **ZIP Vorschau / Debug** Spielkennung und Anzeigenamen eintragen.
3. Das neue installierbare Frontend-ZIP auswählen.
4. **Vorschau erstellen** anklicken.
5. Die Vorschau öffnet unter `/apps/preview-<id>/`.

Die App wird dabei **nicht installiert** und nicht in `platform_registry` eingetragen.

## Isolation

Die Vorschau verwendet eine eigene temporäre Test-API, einen eigenen temporären Spielstand, eine Kopie des gemeinsamen Spieler-/Bereichskatalogs, eigene temporäre Testspieler/-bereiche, einen Preview-Präfix für Browser-Speicher und keinen Service Worker.

Produktive App-Dokumente werden nicht geschrieben.

## Aufbewahrung

Standard:
- `PREVIEW_KEEP_HOURS=24`
- `PREVIEW_MAX=10`

Abgelaufene Vorschauen werden automatisch bereinigt. Jede Vorschau kann im Adminbereich sofort manuell gelöscht werden.

Die Vorschau verwendet denselben ZIP-Validator wie die echte Installation.
