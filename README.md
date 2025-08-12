## dynamic_sof_apps

Tools for Soldier of Fortune dynamic scripting and menu workflows.

### RFM Parser (CLI)
Location: `parsers/rfm_parser/rfm_parser.py`

- Parses `.rmf` (Raven Menu Format) files into chained CVars constrained to 255-characters per `set` line.
- Guarantees:<br>
  - Drops outer `<stm>` and `</stm>` wrappers from payload<br>
  - Keeps single tags `<...>` intact within a single CVar (never split a tag)<br>
  - Splits text content between tags as needed<br>
  - Chains using `<includecvar next_cvar>`

Usage:

```bash
python3 parsers/rfm_parser/rfm_parser.py parsers/rfm_parser/in/example.rmf -o parsers/rfm_parser/out/example.cfg
```

### RFM Viewer & WYSIWYG Editor (GUI)
Location: `apps/rfm_editor/`

- Minimal, early preview editor for `.rmf`:
  - Loads `.rmf`, shows frames outline and a simplified live preview
  - Edits frame width/height/name/tail and basic `<text>`/`<image>` content
  - Saves back to `.rmf` and exports to `.cfg` via the CLI parser

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

Notes:
- The live preview is simplified and not a full RMF runtime. It’s suitable for basic authoring and inspection.
- Advanced areas/keywords are displayed in the outline but not all are editable/previewed yet.
