#!/bin/bash
# Wrapper cron. Script sendiri yang cek window, jadi aman dipanggil sesering apa pun.
# Cuma mencetak output kalau benar-benar dapat reward atau ada error.
cd "$(dirname "$0")" || exit 1
exec python3 zoi_bot.py --once 2>&1
