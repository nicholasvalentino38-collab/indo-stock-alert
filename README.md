# Indonesia Stock Alert Bot via GitHub Actions

Bot ini melakukan scan berkala saham Indonesia menggunakan GitHub Actions lalu mengirim alert ke Telegram jika terdeteksi lonjakan volume dan pergerakan harga.

## Fitur
- Scan saham `.JK` dari Yahoo Finance (`yfinance`)
- Hitung `RVOL` terhadap rata-rata candle sebelumnya
- Filter perubahan harga, turnover bar, dan breakout sederhana
- Kirim alert ke Telegram
- Bisa dijalankan manual (`workflow_dispatch`) atau terjadwal (`schedule`)

## Keterbatasan penting
- Ini bukan VPS real-time 24/7. GitHub Actions lebih cocok untuk scan berkala.
- Jadwal GitHub Actions minimum 5 menit dan berjalan dalam UTC.
- Workflow terjadwal bisa terlambat saat beban GitHub tinggi.
- Data `1m`/`5m` dari Yahoo Finance cocok untuk eksperimen retail, bukan feed bursa profesional.

## Secrets yang harus dibuat di GitHub
Repository → Settings → Secrets and variables → Actions → New repository secret
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

## Variables yang disarankan
- `TICKERS` = `BBCA,BBRI,BMRI,TLKM,ASII,ADRO,MEDC,SMDR`
- `MIN_RVOL` = `3.0`
- `MIN_PRICE_CHANGE_PCT` = `0.8`
- `MIN_BAR_VALUE_IDR` = `1000000000`
- `LOOKBACK_BARS` = `20`
- `BAR_INTERVAL` = `5m`
