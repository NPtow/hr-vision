# Canonical materials reader provenance

Source checkout: `assessment-services/HR Vision`, commit `a7c74d447c5a5aed3f74995b761313fc8f883930`.
Source files (working file SHA-256):
- `services/recruiting-api/selection_interviews.py`: `9053ff7709e95d78e7274e17bcdb208d1b5af217f617bf266865fda18924c7ab`
- `services/recruiting-api/core_source_client.py`: `c4dd7e527c1b99b0011a75cdd5f81731d313af4e41368a2e38bf30de9a449ba9`

`adapter_reader.py` preserves Reader/inventory with additional ready-selection generation/source/recording validation; removes environment factory and redirects imports.
`adapter_core.py` retains only ServiceHTTP, read-only CoreClient methods, SourceMedia.access, CoreMaterialsCatalog read projection and its dependencies. Write commands, webhook, capture, collection, SQL/outbox factories are omitted.
`adapter_errors.py` supplies only the two required error helpers; no domain imports.
`adapter.py` supplies original-caller live-directory authorization with no authorization cache and bounded fixed-origin service transports.
No service credentials are committed. HR_MATERIALS_CORE_URL/KEY and optional HR_MATERIALS_SOURCE_URL/KEY are process environment only.

Directory reads now scan canonical dossier metadata for completed interview owners with active linked available audio/video assets. The full manager directory is validated before and after the bounded scan; no assessment flag, hardcoded candidate/name/count, transcript read or media URL resolution participates in inclusion. All verification errors fail the whole catalogue.
