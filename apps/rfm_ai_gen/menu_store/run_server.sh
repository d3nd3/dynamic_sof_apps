#!/usr/bin/env bash
set -euo pipefail

SERVER_DIR="/home/dinda/.wine/drive_c/users/dinda/Soldier of Fortune"

cd "$SERVER_DIR"
exec wine sof-server-start.bat


