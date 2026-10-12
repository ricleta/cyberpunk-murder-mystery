# Character file format, schema version 1

Character files are plain JSON, written by **Export character** and read by **Import character** /
**Restore from a file**. They are a local backup of what a player authored. They never contain
secrets, relationships, objectives, the killer flag, generation data, or any id.

## Constants

Defined in `backend/services/character_export.py`:

| Constant | Value |
|---|---|
| `FORMAT_CHARACTER` | `cyberpunk-murder-mystery/character` |
| `FORMAT_BUNDLE` | `cyberpunk-murder-mystery/character-bundle` |
| `SCHEMA_VERSION` | `1` |
| `SUPPORTED_SCHEMA_VERSIONS` | `{1}` |

## Single character

```json
{
  "format": "cyberpunk-murder-mystery/character",
  "schema_version": 1,
  "exported_at": "2026-10-11T18:00:00Z",
  "character": {
    "display_name": "Kira Voss",
    "role": {"id": "netrunner", "text": "Netrunner"},
    "description": "...",
    "traits": ["secretive", "mirrorshades"],
    "lifepath": {
      "shared": {"cultural_origin": {"id": "...", "text": "..."}, "personality": null, "...": "all 13 fields"},
      "factions": {
        "friend": {"type": {"id": "...", "text": "..."}, "name": "Free text", "feeling": {"id": "...", "text": "..."}},
        "enemy":  {"type": null, "name": null, "feeling": null}
      },
      "role_specific": {"<table key>": {"id": "...", "text": "..."}, "<other table>": null}
    }
  }
}
```

- Option fields are `{id, text}`; unset values are `null`. `role_specific` lists every table of the role.
- A bundle (`FORMAT_BUNDLE`, GM export) is `{format, schema_version, exported_at, session_name, characters: [<character object>, ...]}`.

## Import rules

- The file is untrusted. Limit 64 KB per character file (bundles are chosen from client-side, up to 2 MB).
- Structural problems are rejected: not an object, wrong `format`, a bundle sent to the single-character
  endpoint, `schema_version` missing / not an integer / not supported (a larger number says the file is
  from a newer version).
- Every value goes through the same validation as editing the character by hand. A bad field is reported as
  `skipped` and the rest still applies.
- Options match by `id`, else by exact case-insensitive `text`. A key present in the file replaces the stored
  value (`null` clears it); a missing key leaves it alone and adds a warning. `role_specific` replaces the whole set.
- Outside the lobby only name, description and traits are applied; role, Lifepath and factions are skipped
  with code `session_locked`.
- `is_killer`, ids and unknown keys are always ignored (listed as warnings).

## Future versions

Change `SCHEMA_VERSION` only for a change old readers cannot understand. Add the new number to
`SUPPORTED_SCHEMA_VERSIONS` and keep a reader for every older version listed there (convert old files to the
current shape before applying). Purely additive keys do not need a new version: unknown keys are ignored with a warning.
