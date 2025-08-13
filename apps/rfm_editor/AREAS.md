RFM Areas Cheat Sheet (Preview Support)

This editor recognizes the following area/rect elements and some of their attributes.

Common attributes supported (parsed and/or shown in Properties):
- key, ckey, ikey
- tint, atint, btint, ctint, dtint
- noshade, noscale, noborder
- border <width> <line width> <line color>
- width, height
- next, prev
- cvar, cvari
- inc, mod
- tip
- xoff, yoff
- tab
- bolt, bbolt
- align
- basic conditionals captured as raw (iflt, ifgt, ifle, ifge, ifne, ifeq, ifset, ifclr)

Elements partially rendered in preview:
- blank
- hr (horizontal rule)
- vbar
- text, ctext (drawn as text)
- image (.m32 and common image formats)
- list
- slider
- ticker (drawn once as colored text)
- input
- setkey
- popup
- selection
- ghoul
- gpm
- filebox
- filereq
- loadbox
- serverbox
- serverdetail
- players
- listfile
- users
- chat
- rooms
- layout tokens: normal, left, right, center

Parsed and listed in outline (not yet drawn):
- font, include (and more to come)

Special case: bghoul
- bghoul can be used as a background element or as an area/rect.
- As an area, it supports the common attributes listed above (key/ckey/ikey, tints, border, width/height, xoff/yoff, etc.).
- Current preview does not render models; bghoul is parsed and shown in the outline with its properties available in the Properties panel.

Notes:
- The preview is not a full engine; many behaviors (e.g., interactive sliders, lists, vbar) are placeholders.
- Raw source mode shows the exact serialized .rmf content as it would be saved.

