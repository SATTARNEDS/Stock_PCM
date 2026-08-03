# PCM Server Installer

ชุดติดตั้งแบ่งเป็น 2 โหมดอย่างชัดเจน และไม่รวมฐานข้อมูลจริงหรือไฟล์ความลับไว้ใน Payload

## โครงสร้าง

```text
PCM_Server_Installer/
├─ 01_INSTALL_NEW_SERVER.bat     ติดตั้งเครื่องใหม่
├─ 02_RESTORE_SERVER.bat         กู้คืนฐานข้อมูลเดิม
├─ BUILD_INSTALLER_PACKAGE.ps1   สร้าง Payload ที่เครื่องพัฒนา
├─ Payload/PCM/                  ไฟล์โปรแกรม ไม่มีฐานข้อมูล
├─ Database_Backup/              วางไฟล์ backup .db ที่นี่
├─ Prerequisites/Python/         วาง Python 3.12 x64 installer
├─ Wheels/                       Python packages สำหรับติดตั้ง Offline
└─ Scripts/                      สคริปต์ภายใน ห้ามย้ายแยก
```

## เตรียมชุดติดตั้งที่เครื่องพัฒนา

1. ดาวน์โหลด Python 3.12 x64 installer จาก python.org แล้ววางใน `Prerequisites/Python/`
2. เปิด PowerShell ในโฟลเดอร์นี้
3. สร้าง Payload:

```powershell
powershell -ExecutionPolicy Bypass -File .\BUILD_INSTALLER_PACKAGE.ps1
```

ถ้าต้องการติดตั้งบนเครื่องที่ไม่มีอินเทอร์เน็ต:

```powershell
powershell -ExecutionPolicy Bypass -File .\BUILD_INSTALLER_PACKAGE.ps1 -IncludeOfflineWheels
```

4. คัดลอกโฟลเดอร์ `PCM_Server_Installer` ทั้งโฟลเดอร์ไปเครื่องใหม่

## โหมด 1: ติดตั้งเครื่องใหม่

ดับเบิลคลิก `01_INSTALL_NEW_SERVER.bat`

ระบบจะติดตั้งไปที่ `C:\PCM` และดำเนินการดังนี้:

- ติดตั้ง Python 3.12 ถ้ายังไม่มี
- คัดลอกโปรแกรมโดยไม่แตะฐานข้อมูล
- สร้าง `.venv` และติดตั้ง dependencies
- สร้าง `.env` พร้อม secret แบบสุ่ม
- เปิด Firewall พอร์ต 5000
- สร้าง Scheduled Tasks สำหรับ Server, Backup และ Watchdog
- สร้าง Shortcut บน Desktop
- ตรวจ Python source และ `/healthz`

## โหมด 2: กู้คืน Server

1. วางไฟล์ backup เช่น `factory_stock_20260803_020000.db` ใน `Database_Backup/`
2. ดับเบิลคลิก `02_RESTORE_SERVER.bat`
3. เลือกไฟล์ `.db`

ระบบจะ:

- ตรวจ `PRAGMA integrity_check`
- ตรวจว่ามีตารางหลักของ PCM
- สำรองฐานข้อมูลปัจจุบันก่อน
- หยุด Server และ Watchdog
- กู้คืนผ่าน SQLite Backup API
- เปิด Server และตรวจ `/healthz`

## Scheduled Tasks

- `PCM-Web-Server-Startup`
- `PCM-Database-Backup-Daily`
- `PCM-Web-Server-Watchdog`

## ข้อสำคัญ

- อย่าเปลี่ยนชื่อหรือย้ายโฟลเดอร์ `Scripts` และ `Payload`
- ห้ามใช้ไฟล์ `factory_stock.db-wal` หรือ `factory_stock.db-shm` เป็นไฟล์กู้คืน
- เก็บ `.env`, `.secret_key` และฐานข้อมูลสำรองไว้นอกเครื่อง Server อีกหนึ่งชุด
- หลังติดตั้ง ให้ตั้งค่า SMTP ใน `C:\PCM\my_factory_app\.env` หากใช้งาน Email Notifications
