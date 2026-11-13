# zoi-auto-sync

Auto-sync [Zoi Network](https://zoi.network) tiap 4 jam. Login pakai email + password, baca kode OTP dari Gmail lewat IMAP, simpan session token, lalu sync otomatis sesuai window resmi Zoi.

Dibuat karena window sync Zoi cuma 4 jam dan streak putus kalau kelewat.

## Disclaimer

**Ini bukan project resmi Zoi.** Tool ini otomatis mengakses API yang dipakai aplikasi Android Zoi. Pakai dengan risiko sendiri.

- API bisa berubah kapan saja tanpa pemberitahuan, dan script ini akan rusak kalau itu terjadi
- Otomasi akun bisa melanggar ToS layanan. Risiko ban ditanggung pengguna
- Jangan pakai buat akun yang kamu tidak punya izin aksesnya

Penulis tidak bertanggung jawab atas akun yang hilang, saldo yang hangus, atau konsekuensi lain.

## Cara kerja

1. `POST /v1/auth/sign-in` dengan email + password + `deviceId`
2. Kalau device belum dipercaya, server minta OTP → script baca dari Gmail via IMAP
3. Simpan `sessionToken` ke disk (mode 600)
4. Tiap kali dijalankan: cek `/v1/sync/state`. Kalau window buka, `POST /v1/sync`
5. Token expired → login ulang otomatis

Reward sync: base 10 ZOI, dikali streak multiplier (hari ke-3 = x1.1, ke-7 = x1.2, sampai ke-365 = x1.7).

## Instalasi

Butuh Python 3.8+. Tidak ada dependency eksternal — semuanya stdlib.

```bash
git clone https://github.com/USERNAME/zoi-auto-sync.git
cd zoi-auto-sync

cp .env.example .env
chmod 600 .env
# edit .env, isi kredensial kamu
```

### Bikin Gmail App Password

OTP dikirim ke email, jadi script butuh akses IMAP:

1. Buka https://myaccount.google.com/apppasswords
2. Butuh 2-Step Verification aktif dulu
3. Bikin app password baru, copy 16 karakter yang muncul
4. Masukkan ke `ZOI_IMAP_PASS` di `.env`

**Jangan pakai password akun Gmail kamu** — harus app password.

### Generate deviceId

```bash
openssl rand -hex 8
```

Pakai hasilnya di `ZOI_DEVICE_ID`. Simpan nilainya — kalau berubah, server akan minta OTP lagi.

## Pemakaian

```bash
# cek saldo, streak, kapan sync berikutnya
python3 zoi_bot.py --status

# sync kalau window udah buka, diam kalau belum (buat cron)
python3 zoi_bot.py --once

# paksa login ulang, buang token cache
python3 zoi_bot.py --login
```

## Setup cron

Jalankan tiap 20 menit. Script sendiri yang ngecek window, jadi aman dipanggil sesering apa pun.

```cron
*/20 * * * * /path/to/zoi-auto-sync/run.sh
```

`run.sh`:

```bash
#!/bin/bash
cd "$(dirname "$0")"
exec python3 zoi_bot.py --once 2>&1
```

Supaya tidak spam notifikasi, `--once` hanya mencetak output kalau benar-benar dapat reward atau ada error.

## Konfigurasi

Semua lewat environment variable, atau file `.env`. Env var menang atas file.

| Variable | Wajib | Default | Keterangan |
|---|---|---|---|
| `ZOI_EMAIL` | ya | — | Email akun Zoi |
| `ZOI_PASSWORD` | ya | — | Password akun Zoi |
| `ZOI_DEVICE_ID` | ya | — | Device ID, 16 char hex |
| `ZOI_IMAP_PASS` | ya | — | Gmail app password |
| `ZOI_IMAP_HOST` | tidak | `imap.gmail.com` | Server IMAP |
| `ZOI_IMAP_USER` | tidak | sama dengan `ZOI_EMAIL` | User IMAP |
| `ZOI_API_BASE` | tidak | `https://api.zoi.network/v1` | Base URL API |
| `ZOI_STATE_DIR` | tidak | `~/.zoi-auto-sync` | Tempat token + log |
| `ZOI_LOG_FILE` | tidak | `<state>/zoi_bot.log` | Path log |
| `ZOI_ENV_FILE` | tidak | — | Path file env tambahan |

Urutan pencarian file env: `ZOI_ENV_FILE` → `./.env` → `~/.config/zoi-auto-sync/.env` → `~/.agent/credentials/zoi.env`.

## Batasan

- **Human verification tidak bisa dilewati.** Kalau Zoi minta verifikasi (`humanVerificationRequired: true`), kamu harus buka app dan verifikasi manual sekali.
- **Butuh akses IMAP Gmail.** Kalau OTP dikirim ke email lain, script perlu disesuaikan.
- **Chest task tidak diotomasi.** Script ini hanya sync. Task chest (join Telegram, follow X, undang teman) dikerjakan manual di app.
- **API tidak terdokumentasi.** Endpoint ditemukan lewat inspeksi bundle aplikasi Android. Bisa berubah kapan saja.

## Lisensi

MIT — lihat [LICENSE](LICENSE).
