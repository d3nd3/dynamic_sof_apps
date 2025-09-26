Menu Store Transport - POC Demo

Quick steps to test end-to-end transport.

Prereqs: SoFplus server + client; ensure custom commands/cvars enabled.

1) On the client, load receiver and progress UI

- In console:
  - sp_sc_exec_file menus/menu_store/ms_transport_client.func
  - sp_sc_func_exec ms_transport_client_init
  - Optional UI: menu menus/menu_store/ms_progress

2) On the server, send demo payload to your client slot

- In console:
  - sp_sc_exec_file menus/menu_store/ms_transport_server.func
  - sp_sc_exec_file menus/menu_store/ms_demo_server.func
  - set _ms_tx_slot <slot>
  - sp_sc_func_exec ms_demo_send

3) Observe on client

- _ms_rx_progress increases (UI bar if open)
- After receipt, demo_installed should be 1 and _ms_demo_installed.cfg saved in menus

Tuning
- _ms_tx_chunk_size default 64 (server clamps to 250)
- _ms_tx_timer_ms default 100

Notes
- Header: FFFFf for func, FFFFr for rmf (stripped on first chunk)
- Chunks execute immediately to avoid 255-char cvar limits.


