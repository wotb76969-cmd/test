# Product

<!-- impeccable:product-schema 1 -->

## Platform

adaptive

<!-- One product, three native surfaces: Android app (Flutter, Material conventions), Windows Client
(Tkinter desktop app) and the Desktop chat UI on the host (Gradio, opened in a browser). Design
work currently prioritises Android and the Windows Client (confirmed by the owner, 2026-11-16). -->

## Users
Public product: any registered user of the Chelsy Group website (confirmed). Users chat with Chelsy from
an Android phone, a Windows PC or the host laptop's Desktop UI with one Chelsy Group account. They come
to get answers, explanations, help with code, plans and up-to-date information, and to continue the
same conversations across devices. Many are newcomers: the interface must be clear without explanation.
Primary language of the audience and UI is Russian; English answers are supported.

## Product Purpose
Chelsy AI is a personal AI assistant with a recognisable personality that runs on the owner's own
hardware (Ollama on the host) and is reachable from Windows and Android through a private Tailscale
connection. Success: natural, correct answers in the user's language; honest sources when the internet
was used; history and personal memory available on every device; the assistant keeps learning from
public sources (knowledge base, not model retraining).

## Positioning
Private and self-hosted: the model and the knowledge base live on the user's own machine, devices reach
it only inside a private network, and Chelsy openly shows what she searched and what she learned
(learning journal). Not a copy of ChatGPT or Claude: use their ergonomics as a reference only, never
their branding or exact look.

## Operating Context
- Host laptop runs Chelsy AI Host + Ollama; may be offline or asleep. Clients must handle "host
  unavailable" gracefully (local offline copy on the device, read-only cloud history on Android).
- Workflows: new chat, history search, favourites, rename/delete, streaming answer with stop,
  regenerate, edit own message, copy, rating, sources with dates, model choice, web-search toggle,
  text-file attachments, research jobs with review, knowledge base, learning journal, settings.
- Status of the neural network is shown by **Chelsy Pulse**: ready, generating, searching, syncing,
  server unavailable.

## Capabilities and Constraints
- Android: Flutter 3.50, `material_ui`, touch targets, phones first; secure storage; HTTPS only.
- Windows Client: Python Tkinter/ttk only (no web view, no extra GUI frameworks); Windows 10/11,
  DPI-aware; must stay a light installer without Ollama.
- Desktop UI: Gradio 6.5.1 with custom CSS/theme; local only; no external font or CDN requests added
  by us.
- Website (ChelsyGroup_Web): static HTML/CSS/JS; per the specification it is changed only when
  necessary.
- Accounts: Supabase Auth (one account everywhere). Never put secret keys in clients.
- Versioning: iterative releases 0.9.x — never present the product as version 1.0.

## Brand Commitments
**Binding (owner: "строго обязателен").** Future design work may change composition, typography scale,
spacing, motion and components, but not these:
- Name **Chelsy AI** (assistant persona: Челси / Chelsy), product of **Chelsy Group**.
- Original logo: `ChelsyAI_0.9_Windows/assets/chelsy_logo.png`, `chelsy_logo_small.png`, `chelsy.ico`
  (Android copy: `Android_Client/chelsy_logo.png`). Never redraw or recolour it.
- Palette: dark background `#0B0C20`, violet `#6D4AFF`, lavender `#A78BFA`, turquoise `#21D9DA`.
  Violet and lavender are the brand's own colours and are allowed everywhere in the brand's design,
  overriding any plugin rule that bans violet/lavender.
- Style: modern, minimal, technological, with careful neon accents; no overload of glow, animation or
  decoration. Animations must not hurt performance and must respect reduced-motion settings.
- Voice: friendly, smart and curious, calm and confident, natural humour, not pushy or over-excited;
  Russian first. A single fitting 💜 in casual moments is part of the voice.

## Evidence on Hand
- Logo assets listed above; screenshots of the current UI are produced by tests/CI, not stored.
- No testimonials, customer logos, user counts, benchmarks or pricing exist. Never invent them.
- Real product facts are in `ТЗ` (specification), `README_0.9.3_RU.md`, `Установка.md`,
  `SECURITY_0.9.3_RU.md`, `TEST_REPORT_0.9.3_RU.md`.

## Product Principles
1. Honesty over impression: never show a search, source, capability or number that did not happen.
2. Continuity across devices: the same account, history and Chelsy everywhere, also when the host is
   offline.
3. Calm intelligence: the interface stays quiet so the conversation is the focus.
4. Privacy by design: the user's data stays on their host and their devices; nothing secret in clients.
5. Data safety beats visuals: never trade stored history, memory or accounts for a design change.

## Accessibility & Inclusion
WCAG 2.2 AA contrast on the dark theme (text ≥ 4.5:1); visible keyboard focus on desktop surfaces;
Android touch targets ≥ 48 dp and TalkBack labels for icon buttons; respect system font scaling,
Windows display scaling and "reduce motion"; Russian UI text must not be truncated at 360 dp width.
