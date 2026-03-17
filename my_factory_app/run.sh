#!/bin/sh
set -e

# กู้คืนฐานข้อมูล (ดึงจาก Cloudflare R2)
echo "🔄 กำลังตรวจสอบและกู้คืนฐานข้อมูลจาก Cloud..."
litestream restore -if-db-not-exists -if-replica-exists ./factory_stock.db

# รันระบบสำรองข้อมูลพร้อมกับ Flask
echo "🚀 เริ่มต้นระบบ Stock PCM..."
exec litestream replicate -exec "gunicorn --bind 0.0.0.0:$PORT app:app"