# Chelsy AI — Design System MASTER (ui-ux-pro-max source of truth)

> Written for the `ui-ux-pro-max` plugin's Master + Overrides pattern. This file is the global source of
> truth for every surface. Page/surface files in `pages/` may refine it but never change the brand
> palette or the logo (binding brand commitments in `/PRODUCT.md`).
>
> Note: the plugin's generic `--design-system` recommendation for "AI assistant chat app" (rose/orange
> palette, landing-page hero + testimonials pattern) was reviewed on 2026-11-16 and **rejected**: it
> conflicts with the binding brand palette and with the product (an app, not a landing page; no
> testimonials exist). Use the plugin's searches for UX rules, components, charts and stack guidelines,
> not for palettes or brand.

## 1. Brand tokens (binding)

| Token | Value | Use |
|---|---|---|
| `bg` | `#0B0C20` | app background (all surfaces, also Android splash) |
| `panel` | `#11132B` | sidebar, drawers, cards |
| `panel-2` | `#161937` | inputs, composer, raised surfaces |
| `stroke` | `#262A52` | borders, dividers |
| `violet` (primary) | `#6D4AFF` | primary actions, send button, selection, focus on Android |
| `lavender` (secondary) | `#A78BFA` | author labels, links, generating state |
| `turquoise` (accent) | `#21D9DA` | ready/searching state, focus ring on desktop, progress |
| `text` | `#ECEBFA` | primary text |
| `muted` | `#A3A6C8` | secondary text, hints |
| `user-bubble` | `#231D4F` (border `#3A2F7D`) | user messages |
| `code-bg` | `#0E1029` | code blocks |
| `danger` | `#FF8A65` | offline / error state |
| `selection` | `#2A2160` | selected list rows |

Contrast (WCAG, computed 2026-11-16) on `bg`: `text` 16.4:1, `muted` 8.1:1, `lavender` 7.1:1, `turquoise` 11.0:1,
`danger` 8.3:1; `muted` on `panel-2` 7.2:1; `text` on `user-bubble` 13.1:1. Violet `#6D4AFF` on `bg` is 3.75:1 —
use it for fills, icons and large text (≥ 3:1), not for body text; white text on violet fills 5.15:1 (OK).

## 2. Typography

- Windows / Desktop: `Segoe UI` (`Segoe UI Semibold` for titles), code `Cascadia Code` → `Consolas`.
- Android: platform default (Roboto / device font) via Material; respects system font scale.
- No web fonts loaded from external CDNs in the Desktop UI.
- Scale: chat body 15 px/sp (line-height 1.5–1.6), meta 11–12, titles 17–22, hero greeting 22–24.

## 3. Shape, spacing, depth

- Radii: inputs/composer 14–18, cards/bubbles 16, list rows 10, avatars round, logo tiles 9–24.
- Spacing base 4; common 8 / 12 / 16 / 24.
- Depth by tonal layering (`bg` → `panel` → `panel-2`), thin `stroke` borders; glow only as a soft
  violet halo on the logo/composer focus. No heavy shadows.

## 4. Signature component: Chelsy Pulse

Small dot + expanding ring. States and colours: ready `turquoise` (static/breathing), generating
`lavender` (ring), searching `turquoise` (ring), syncing `violet` (ring), offline `danger` (static).
One ring, ≤ 1.6 s cycle, disabled with reduced motion. Implementations: `app.py` (CSS),
`client_gui.py` (`Pulse` canvas), `Android_Client/lib/src/pulse.dart`.

## 5. UX rules for every surface

- The conversation is the hero: wide readable column (≤ 760–900 px), messages left (Chelsy, with logo
  avatar) and right (user bubble).
- Composer always reachable; send ↔ stop toggle during generation; Enter sends on desktop, Shift+Enter
  newline.
- Sources appear as a separate block with dates; never fake sources.
- Offline states are explicit banners with a retry action; read-only copies are labelled as such.
- Russian copy, short and plain; no emoji as icons (use platform icons); 💜 only in Chelsy's voice.
- Accessibility per `/PRODUCT.md`.

## 6. Where the tokens live in code

- Desktop: `ChelsyAI_0.9_Windows/app.py` → `CSS` and `THEME_VALUES`.
- Windows Client: `ChelsyAI_0.9_Windows/client_gui.py` → constants `BG … MUTED`, `_style()`.
- Android: `Android_Client/lib/src/theme.dart` → `ChelsyColors`, `chelsyTheme()`.
- Website: `ChelsyGroup_Web/assets/styles.css`.
Change a token in all of them together.
