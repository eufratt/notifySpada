# SPADA Presensi Notifier

Bot yang ngecek halaman presensi SPADA dan kirim notif ke HP lewat ntfy.
Bot TIDAK absen otomatis, kamu tetap klik sendiri.

## 1. Setup ntfy (HP)
1. Install app **ntfy** (Android / iOS).
2. Subscribe ke topic yang susah ditebak, contoh: `spada-budi-x7k29q`.
   (Topic ntfy.sh itu publik, siapa tahu namanya bisa baca notifnya.)

## 2. Deploy ke Render
1. Upload folder ini ke repo GitHub (private lebih baik).
2. Render -> New -> Web Service -> pilih repo. (Atau pakai Blueprint dari `render.yaml`.)
   - Build command: `pip install -r requirements.txt`
   - Start command: `gunicorn app:app --bind 0.0.0.0:$PORT --timeout 90`
   - Plan: Free
3. Isi Environment Variables:

| Key | Isi |
|---|---|
| `SPADA_USERNAME` | NIM / username SPADA |
| `SPADA_PASSWORD` | password SPADA |
| `ATTENDANCE_URLS` | URL presensi, pisah koma. Contoh: `https://spada.upnyk.ac.id/mod/attendance/view.php?id=838695,https://spada.upnyk.ac.id/mod/attendance/view.php?id=XXXXXX` |
| `NTFY_TOPIC` | topic ntfy tadi |
| `CHECK_KEY` | string acak panjang, bikin sendiri |

## 3. Cron di cron-job.org
- URL: `https://NAMA-APP.onrender.com/check?key=ISI_CHECK_KEY`
- Interval: tiap 5 menit (atau 10)
- Timeout request: 60 detik (Render free butuh ~30-50 detik bangun dari tidur)

## 4. Tes
Buka URL `/check?key=...` di browser. Hasilnya JSON, misal:
`{"results":[{"course":"...","open":0,"notified":0}]}`
`open` = jumlah sesi presensi yang terbuka, `notified` = notif yang dikirim.

## Catatan
- Notif dikirim sekali per sesi. State disimpan di /tmp, jadi kalau Render restart bisa kena notif ulang sekali.
- Kalau SPADA ganti tampilan, deteksi link presensi bisa perlu disesuaikan.
- Kalau login gagal terus dari Render, kemungkinan SPADA memblokir IP server.
- Tambah mata kuliah lain: tambahkan URL presensinya ke `ATTENDANCE_URLS`.

## Error SSL (CERTIFICATE_VERIFY_FAILED)
Server SPADA tidak mengirim sertifikat intermediate. Dua solusi:
- **Cepat (kurang aman):** di Render tambah env `SPADA_VERIFY_SSL` = `false`.
  Koneksi tetap terenkripsi, tapi identitas server tidak diverifikasi.
- **Aman:** export sertifikat intermediate SPADA dari browser (klik gembok -> Certificate -> Details -> pilih sertifikat di tengah rantai -> Export PEM), simpan sebagai `intermediate.pem` di root repo, lalu tambah env `SPADA_EXTRA_CERT` = `intermediate.pem`.