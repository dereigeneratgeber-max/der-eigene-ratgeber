# Der eigene Ratgeber – Produktion

Dieses Repository erzeugt aus einem fertigen Beitrag (JSON vom Autor-Prompt) automatisch:
- ein Video (9:16) mit ElevenLabs-Stimme, großer Aussage und Wort-für-Wort-Untertiteln, oder
- ein Karussell (4:5) bei den Serien „Was die Forschung sagt“ und „Satz aus dem Buch“.

Stile: himmel (positiv), tusche (tiefgründig/skeptisch), klar (Forschung). Der Stil steht im Feld „stil“ des Beitrags.

## Einmalige Einrichtung
Settings → Secrets and variables → Actions
- Secret `ELEVENLABS_API_KEY`: API-Schlüssel von ElevenLabs
- Variable `ELEVENLABS_VOICE_ID`: ID der Stimme

## Testen
Actions → „Beitrag produzieren“ → „Run workflow“. Das Ergebnis liegt nach ein paar Minuten unten auf der Seite des Laufs unter „Artifacts“ zum Herunterladen.

## Später
Make startet den Ablauf automatisch über die GitHub-Schnittstelle (repository_dispatch, Ereignis `produzieren`, Beitrag im Feld `beitrag`).

Schriften: Poppins und Lora (Google Fonts, SIL Open Font License), werden beim Lauf aus dem Google-Fonts-Archiv geladen. Das Cover ist Eigentum des Autors.
