# Deploy on Windows PC (Server Mode)

คู่มือนี้สำหรับติดตั้งระบบบน Windows 1 เครื่องให้ทำงานเป็นเว็บเซิร์ฟเวอร์ภายในองค์กร

## 1) เปิด PowerShell แบบ Administrator

แนะนำให้เปิดแบบ Admin เพื่อให้สคริปต์ตั้ง Firewall และ Scheduled Task ได้ครบ

## 2) เข้าโฟลเดอร์โปรเจกต์

```powershell
cd C:\Users\sattarned\Documents\GitHub\PCM
```

## 3) รันสคริปต์ติดตั้งอัตโนมัติ

```powershell
powershell -ExecutionPolicy Bypass -File .\setup_windows_server.ps1
```

สคริปต์จะทำสิ่งต่อไปนี้อัตโนมัติ:
- สร้าง `.venv`
- ติดตั้งแพ็กเกจจาก `requirements.txt`
- สร้างไฟล์ `my_factory_app/.env` จาก `my_factory_app/.env.server.example` (ถ้ายังไม่มี)
- เปิดพอร์ต `5000` บน Firewall
- สร้าง Scheduled Task 2 ตัว
  - `PCM-Web-Server-Startup` เริ่มเว็บตอนบูตเครื่อง
  - `PCM-Database-Backup-Daily` สำรอง DB ทุกวันเวลา 02:00

## 4) แก้ค่าในไฟล์ .env

ไฟล์ที่ต้องแก้:
- `my_factory_app/.env`

ค่าอย่างน้อยที่ควรตั้ง:
- `FLASK_SECRET_KEY` ใส่ค่า random ยาวๆ
- `FLASK_DEBUG=0`
- ค่า SMTP (ถ้าต้องการส่งอีเมลแจ้งเตือน)

## 5) ทดสอบรันด้วยมือ

```powershell
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

ทดสอบเข้าเว็บ:
- เครื่องเดียวกัน: `http://127.0.0.1:5000`
- เครื่องอื่นใน LAN: `http://<SERVER_IP>:5000`

## 6) คำสั่งเสริม

รันสำรอง DB ด้วยมือ:

```powershell
powershell -ExecutionPolicy Bypass -File .\backup_factory_db.ps1
```

สำรองและเก็บล่าสุด 60 ไฟล์:

```powershell
powershell -ExecutionPolicy Bypass -File .\backup_factory_db.ps1 -KeepLatest 60
```

## 7) หมายเหตุ

- หากไม่ได้เปิด PowerShell แบบ Admin, สคริปต์ setup จะติดตั้ง Python dependencies ให้ได้ แต่จะข้ามการตั้ง Firewall/Task
- ระบบใช้ SQLite (`my_factory_app/factory_stock.db`) เหมาะกับการรันบนเครื่องเซิร์ฟเวอร์เดียว
