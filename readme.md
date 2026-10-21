# SPADA Presensi Notifier

Bot yang ngecek halaman presensi SPADA dan kirim notif ke HP lewat ntfy.
Bot TIDAK absen otomatis, kamu tetap klik sendiri.

## 1. Setup ntfy (HP)
1. Install app **ntfy** (Android / iOS).
2. Subscribe ke topic yang susah ditebak, contoh: `spada-budi-x7k29q`.
   (Topic ntfy.sh itu publik, siapa pun yang tahu namanya bisa baca notifnya.)

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
| `COURSE_URLS` | URL halaman utama mata kuliah, pisah koma. Bot mencari SEMUA modul presensi di halaman itu (cocok untuk matkul yang bikin link presensi baru tiap minggu). Contoh: `https://spada.upnyk.ac.id/course/view.php?id=51593` |
| `ATTENDANCE_URLS` | (opsional) URL presensi tertentu, pisah koma. Contoh: `https://spada.upnyk.ac.id/mod/attendance/view.php?id=799211` |
| `NTFY_TOPIC` | topic ntfy tadi |
| `CHECK_KEY` | string acak panjang, bikin sendiri |
| `SPADA_VERIFY_SSL` | `false` kalau kena error sertifikat (lihat bawah) |

Salah satu dari `COURSE_URLS` atau `ATTENDANCE_URLS` harus diisi. Boleh dua-duanya, URL yang sama tidak dicek dua kali.

## 3. Cron di cron-job.org
- URL: `https://NAMA-APP.onrender.com/check?key=ISI_CHECK_KEY`
- Method: GET
- Interval: tiap 5 menit (atau 10)
- Timeout request: 60 detik (Render free butuh ~30-50 detik bangun dari tidur)

## 4. Tes
Buka URL `/check?key=...&verbose=1` di browser. Hasilnya JSON, misal:

```
{"checked": 10,
 "courses": [{"course_url": "...id=51593", "modules_found": 10}],
 "results": [{"activity": "Kehadiran 9", "open": 0, "notified": 0, ...}],
 "errors": []}
```
- `modules_found` = jumlah modul presensi yang ketemu di halaman matkul. Kalau 0 atau jauh lebih sedikit dari yang kamu lihat di SPADA, kabari aku.
- `open` = jumlah sesi presensi yang terbuka, `notified` = notif yang dikirim.
- Tanpa `&verbose=1`, `results` cuma menampilkan modul yang sedang terbuka.

## Catatan
- Notif dikirim sekali per sesi. State disimpan di /tmp, jadi kalau Render restart bisa kena notif ulang sekali.
- Kalau SPADA ganti tampilan, deteksi link presensi bisa perlu disesuaikan.
- Kalau login gagal terus dari Render, kemungkinan SPADA memblokir IP server.
- Tambah mata kuliah lain: tambahkan URL halaman utamanya ke `COURSE_URLS`.

## Error SSL (CERTIFICATE_VERIFY_FAILED)
Server SPADA tidak mengirim sertifikat intermediate. Dua solusi:
- **Cepat (kurang aman):** di Render tambah env `SPADA_VERIFY_SSL` = `false`.
  Koneksi tetap terenkripsi, tapi identitas server tidak diverifikasi.
- **Aman:** export sertifikat intermediate SPADA dari browser (klik gembok -> Certificate -> Details -> pilih sertifikat di tengah rantai -> Export PEM), simpan sebagai `intermediate.pem` di root repo, lalu tambah env `SPADA_EXTRA_CERT` = `intermediate.pem`.