# Project instructions

## Translation quality gate (mandatory)

Runvard supports `de`, `en`, `fr`, `it`, `es`, and `pt`. Every project change must preserve complete and consistent translations for all six languages.

- After every change, run `node scripts/check-i18n.mjs`, even when the change does not appear to affect visible text. Do not report the work as complete while this command fails.
- Any new or changed user-visible text must be added or updated in all six locales in the same change. Never leave a copied English or German fallback as a substitute for a reviewed translation.
- Keep locale dictionaries key-identical. Preserve interpolation variables and placeholders exactly in every locale.
- Review translations for meaning, grammar, context, and consistent terminology. Key parity alone is not sufficient.
- Do not hardcode user-visible text in runtime rendering paths. Route it through the existing translation helpers and catalogs.
- Reuse the established glossary for technical terms. Do not mix competing translations for the same UI concept within one locale.
- When a translation is corrected, add or extend a quality assertion in `scripts/check-i18n.mjs` when practical so the error cannot silently return.
- For changes to visible UI text, also inspect the affected screen in every supported language. At minimum, verify the changed flow in a real browser and check for mixed-language fragments, clipped text, and broken placeholders.
- The full Python suite includes `tests/test_i18n.py`; run the relevant tests during development and run `.venv/bin/python -m pytest -q` before final completion when the change is broader than translations alone.

Required completion evidence for translation-related changes:

```bash
node scripts/check-i18n.mjs
.venv/bin/python -m pytest -q tests/test_i18n.py
```
