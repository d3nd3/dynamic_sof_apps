## dynamic_sof_apps

Tools for Soldier of Fortune dynamic scripting and Raven Menu Format (RMF) authoring. This repository contains:

- RFM Parser (CLI) for converting `.rmf` into chained CVars suitable for game config
- RFM Viewer & WYSIWYG Editor (GUI) for visually editing `.rmf` files

### Project structure

- `apps/rfm_editor/` — GUI editor implementation
  - `main.py` — application entrypoint and UI
  - `rfm_parser.py` — lightweight RMF tokenizer and model builder for the editor
  - `rfm_renderer.py` — simplified preview renderer
  - `rfm_serializer.py` — converts in-memory model back to `.rmf`
  - `m32lib.py` — helper for `.m32` image previews
- `parsers/rfm_parser/rfm_parser.py` — CLI converter from `.rmf` to `.cfg`

### RFM Parser (CLI)
Location: `parsers/rfm_parser/rfm_parser.py`

- Parses `.rmf` (Raven Menu Format) files into chained CVars constrained to 255 characters per `set` line
- Output preserves tag integrity (never splits inside `<...>`) and splits only free text between tags
- Uses `<includecvar next_cvar>` chaining for multi-CVar payloads
- Drops outer `<stm>` wrappers from the payload content

Usage:

```bash
python3 parsers/rfm_parser/rfm_parser.py <input.rmf> -o <output.cfg>
```

### RFM Viewer & WYSIWYG Editor (GUI)
Location: `apps/rfm_editor/`

Features:
- Load `.rmf`, show frames in an outline and a simplified live preview
- Edit frame width/height/name/tail; edit basic `<text>` and `<image>` elements
- Save back to `.rmf`; export to `.cfg` using the CLI parser
- Screen ratio preset: 4:3, 16:9, 16:10
- Sub-frame rendering: optional inlining of `page` documents into frame containers
- Include support: `<include file>` is expanded for preview and raw-expansion when requested
- Exinclude support: `<exinclude cvar page_zero page_nonzero>` rendered based on a toggle in the outline
  - Toggle entry under each document root: “Exinclude: Zero/Non-zero”
  - Raw view option “Replace <include> in Raw” respects the exinclude toggle
- Paths for images are resolved using configured Menu Directory and Resource Directory
- Recent files, Open-from-Menu-Directory browser, and backdrop preview

Install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Run the editor:

```bash
python3 -m apps.rfm_editor.main
```

Settings (Menu → Settings):
- Set Menu Directory: base for relative `.rmf` references and menu images (e.g. `pics/menus/...`)
- Set Resource Directory: additional root for resolving images and `.m32` files
- Render Sub-frames: when enabled, renders each frame’s `page` inside its area
- Screen Ratio: choose max screen height profile for the preview (width is fixed at 640)
- Replace `<include>` in Raw: when Raw mode is on, shows expanded includes and exinclude according to the toggle

Usage tips:
- Outline pane shows Documents, Frames, Backdrop, and Elements
- Click a frame or element to see properties; edits update the live preview
- Click “Exinclude: Zero/Non-zero” to switch which branch of `<exinclude>` is rendered
- Use View → Raw .rmf Mode to inspect the underlying file or the expanded raw (if “Replace `<include>` in Raw” is enabled)

Limitations and notes:
- The preview is simplified; not all RMF features are rendered
- Many advanced areas are listed in the outline but are not fully editable/previewed
- Image resolution prefers configured roots and tries common extensions; `.m32` is supported

Troubleshooting:
- Frames not showing: ensure the file is wrapped in `<stm ...>...</stm>` or try reloading; the editor now accepts `<stm>` with attributes (e.g. `<stm resize 640 480>`) and also tolerates absent wrappers
- Empty raw when replacing includes: verify the Menu/Resource directories so included files can be found; the exinclude raw-expansion follows the current toggle

