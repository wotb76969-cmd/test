# Chelsy AI — project instructions

Iterative release line **0.9.x** (never call it 1.0). Specification and docs are in Russian; answer the
owner in Russian.

## Design plugins (installed for this project in `.claude/settings.json`)

| Plugin | Use it for | Source of truth it reads |
|---|---|---|
| **impeccable** (`/impeccable …`) | critique, audit, polish, layout, motion, accessibility of app screens; native Android rules | `PRODUCT.md` (product + binding brand), `DESIGN.md` (create later with `/impeccable document`) |
| **ui-ux-pro-max** | UX rules, component patterns, charts, stack guidelines (`--stack flutter`), accessibility checklists | `design-system/chelsy-ai/MASTER.md` + `pages/*.md` |
| **taste-skill** (`design-taste-frontend`) | only marketing / company pages of `ChelsyGroup_Web` | `PRODUCT.md`, `MASTER.md` |

Priority surfaces for design work (owner, 2026-11-16): **Android app** and **Windows Client**. The
Desktop (Gradio) UI next; the website only when necessary (specification rule).

### Precedence (read before using any design plugin)
1. **Brand is binding** (`PRODUCT.md → Brand Commitments`): palette `#0B0C20 / #6D4AFF / #A78BFA /
   #21D9DA`, original logo, name Chelsy AI. Plugin palettes, "world" rolls or generated design systems
   never replace them.
2. taste-skill's rule "never violet or lavender" **does not apply to Chelsy**: violet and lavender are
   the client's own brand colours (the skill itself keeps a client's own colours via `start.mjs brand`).
   If its gate reports violet, keep the brand colours and mention the report; do not recolour.
3. ui-ux-pro-max `--design-system` output is advisory; do not `--persist` it over `MASTER.md`.
4. Do not copy ChatGPT/Claude branding or exact layouts; use them only as ergonomic references.
5. Data safety and existing functionality beat visual changes; never remove working features.

### Surface constraints
- Android: Flutter 3.50 with `package:material_ui`; see `design-system/chelsy-ai/pages/android.md`.
- Windows Client: Tkinter/ttk only; see `design-system/chelsy-ai/pages/windows-client.md`.
- Desktop: Gradio 6.5.1, CSS + theme in `app.py`; no external fonts/CDNs added by us.
- Platform is `adaptive` in `PRODUCT.md`: impeccable loads Android guidance for Android work.

## Checks before committing
- `cd ChelsyAI_0.9_Windows && python -m unittest discover` (all green; never disable a failing test).
- `cd Android_Client && flutter analyze && flutter test`.
- Push triggers `.github/workflows/build-clients.yml` (tests → Windows installers → signed APK).
