# Deploy on Windows PC (Server Mode)

คู่มือนี้ใช้สำหรับติดตั้งระบบบนเครื่อง Windows ใหม่แบบละเอียด ตั้งแต่เตรียมเครื่องจนพร้อมใช้งานจริงภายในเครือข่าย

## ภาพรวม

เมื่อติดตั้งเสร็จ ระบบจะมีองค์ประกอบหลักดังนี้
- Web app รันที่พอร์ต `5000`
- เปิดรับการเข้าถึงจาก LAN ผ่าน Firewall rule
- รันอัตโนมัติทุกครั้งที่เครื่องเปิด (Scheduled Task)
- สำรองฐานข้อมูลอัตโนมัติทุกวันเวลา 02:00

## 1) เตรียมเครื่องก่อนติดตั้ง

1. ใช้ Windows 10/11 ที่อัปเดตล่าสุด
2. ติดตั้ง Python 3.12 (แนะนำ) และเลือก Add Python to PATH
3. คัดลอกโปรเจกต์ลงเครื่องใหม่ให้ครบทั้งโฟลเดอร์
4. แนะนำตั้งชื่อเครื่องและ IP ให้คงที่ (Static IP) เพื่อให้ผู้ใช้เข้า URL เดิมได้ตลอด

## 2) เปิด PowerShell แบบ Administrator

ต้องเปิดแบบ Admin เพื่อให้สคริปต์ตั้งค่า Firewall และ Scheduled Task ได้ครบ

## 3) เข้าโฟลเดอร์โปรเจกต์

```powershell
cd C:\Users\sattarned\Documents\GitHub\PCM
```

## 4) รันสคริปต์ติดตั้งอัตโนมัติ

```powershell
powershell -ExecutionPolicy Bypass -File .\setup_windows_server.ps1
```

สคริปต์จะทำให้อัตโนมัติ
- สร้าง virtual environment ที่ `.venv`
- ติดตั้งแพ็กเกจจาก `requirements.txt`
- สร้าง `my_factory_app/.env` จาก `my_factory_app/.env.server.example` ถ้ายังไม่มี
- เปิด Windows Firewall inbound สำหรับพอร์ต `5000`
- สร้าง Scheduled Task
  - `PCM-Web-Server-Startup`
  - `PCM-Database-Backup-Daily`

## 5) ปรับค่าคอนฟิกในไฟล์ .env

ไฟล์ที่ต้องแก้
- `my_factory_app/.env`

ค่าที่สำคัญขั้นต่ำ
- `FLASK_SECRET_KEY` ต้องเปลี่ยนเป็นค่าสุ่มยาวและเดายาก
- `FLASK_DEBUG=0`
- `SESSION_COOKIE_SECURE=0` สำหรับ HTTP ภายใน (ถ้าใช้ HTTPS ค่อยปรับเป็น `1`)

ค่าที่แนะนำ
- `APP_BASE_URL=http://<SERVER_IP>:5000`
- ตั้งค่า SMTP ให้ครบถ้าต้องการส่งอีเมลแจ้งเตือน
  - `SMTP_HOST`
  - `SMTP_PORT`
  - `SMTP_USERNAME`
  - `SMTP_PASSWORD`
  - `SMTP_FROM`
  - `SMTP_USE_TLS=1`

## 6) ทดสอบรันแบบ manual ครั้งแรก

```powershell
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

ทดสอบเข้าเว็บ
- จากเครื่อง server: `http://127.0.0.1:5000`
- จากเครื่องอื่นใน LAN: `http://<SERVER_IP>:5000`

ถ้าเข้าไม่ได้จากเครื่องอื่น ให้ตรวจ
- เครื่อง server กับเครื่องลูกอยู่ subnet เดียวกัน
- Firewall เปิดพอร์ต `5000` แล้ว
- ไม่มีซอฟต์แวร์อื่นใช้พอร์ตนี้ซ้ำ

## 7) ตรวจสอบ Scheduled Tasks หลังติดตั้ง

เปิด Task Scheduler แล้วตรวจว่ามี 2 งานนี้
- `PCM-Web-Server-Startup`
- `PCM-Database-Backup-Daily`

แนะนำคลิก Run เพื่อทดสอบด้วยมืออย่างน้อย 1 ครั้ง

## 8) ตรวจสอบการสำรองข้อมูล

สคริปต์สำรองฐานข้อมูล
- `backup_factory_db.ps1`

สำรองด้วยมือ

```powershell
powershell -ExecutionPolicy Bypass -File .\backup_factory_db.ps1
```

เก็บไฟล์สำรองล่าสุด 60 ไฟล์

```powershell
powershell -ExecutionPolicy Bypass -File .\backup_factory_db.ps1 -KeepLatest 60
```

ตำแหน่งไฟล์ backup
- `my_factory_app/backups`

## 9) ตัวเลือกเพิ่มเติมของสคริปต์ติดตั้ง

ข้าม Scheduled Task

```powershell
powershell -ExecutionPolicy Bypass -File .\setup_windows_server.ps1 -SkipTasks
```

ข้าม Firewall

```powershell
powershell -ExecutionPolicy Bypass -File .\setup_windows_server.ps1 -SkipFirewall
```

เปลี่ยนพอร์ตที่ต้องการเปิด

```powershell
powershell -ExecutionPolicy Bypass -File .\setup_windows_server.ps1 -ListenPort 5001
```

กำหนด Python command เอง

```powershell
powershell -ExecutionPolicy Bypass -File .\setup_windows_server.ps1 -PythonCommand "python"
```

## 10) วิธีเริ่ม/หยุดระบบสำหรับงานดูแล

เริ่มระบบ

```powershell
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

หยุดระบบ
- กด `Ctrl + C` ในหน้าต่าง PowerShell ที่กำลังรันอยู่

## 11) Troubleshooting ที่พบบ่อย

### รัน setup แล้วบอกว่าไม่ใช่ Administrator

อาการ
- สคริปต์ข้ามการตั้ง Firewall/Task

วิธีแก้
- ปิดหน้าต่างเดิมแล้วเปิด PowerShell แบบ Run as administrator

### เข้าเว็บได้เฉพาะเครื่อง server

วิธีตรวจ
1. เช็ก IP server ด้วย `ipconfig`
2. ทดสอบ `http://<SERVER_IP>:5000`
3. ตรวจ Windows Firewall ว่ามี rule ของ PCM พอร์ต 5000

### SMTP ส่งเมลไม่ออก

วิธีตรวจ
1. ตรวจค่าทุกตัวใน `.env`
2. ตรวจพอร์ต/การเข้ารหัสจากผู้ให้บริการเมล
3. ตรวจว่าเครื่อง server ออกอินเทอร์เน็ตได้

## 12) เช็กลิสต์ก่อนส่งมอบใช้งาน

- ติดตั้งผ่าน `setup_windows_server.ps1` สำเร็จ
- แก้ `.env` แล้วและตั้ง `FLASK_DEBUG=0`
- เข้าเว็บได้จากเครื่องอื่นใน LAN
- Scheduled Task 2 ตัวมีอยู่จริงและ Run ได้
- ทดสอบ backup แล้วพบไฟล์ใน `my_factory_app/backups`

## หมายเหตุสำคัญ

- ระบบใช้ SQLite (`my_factory_app/factory_stock.db`) เหมาะกับการรันบนเซิร์ฟเวอร์เครื่องเดียว
- ก่อนอัปเดตเวอร์ชันหรือย้ายเครื่อง ควร backup ฐานข้อมูลทุกครั้ง
