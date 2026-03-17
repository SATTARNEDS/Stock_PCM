# 1. ใช้ Python Image รุ่นล่าสุดที่เสถียร
FROM python:3.12-slim

# 2. ติดตั้งเครื่องมือพื้นฐาน และ Library สำหรับจัดการรูปภาพ (Pillow)
RUN apt-get update && apt-get install -y \
    curl \
    ca-certificates \
    libjpeg-dev \
    zlib1g-dev \
    libpng-dev \
    && rm -rf /var/lib/apt/lists/*

# 3. ติดตั้ง Litestream
ADD https://github.com/benbjohnson/litestream/releases/download/v0.3.13/litestream-v0.3.13-linux-amd64.deb /tmp/litestream.deb
RUN dpkg -i /tmp/litestream.deb

# 4. ตั้งค่าโฟลเดอร์ทำงานหลัก
WORKDIR /app

# 5. ติดตั้ง Library (ใช้ไฟล์ข้างนอกสุดตามภาพของคุณ)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install gunicorn

# 6. คัดลอกโค้ดทั้งหมด
COPY . .

# 7. คัดลอกไฟล์คอนฟิกไปที่ตำแหน่งมาตรฐานของระบบ
# สมมติไฟล์คุณอยู่ที่ my_factory_app/etc/litestream.yml
RUN mkdir -p /etc && cp /app/my_factory_app/etc/litestream.yml /etc/litestream.yml

# 8. ย้ายตำแหน่งทำงาน
WORKDIR /app/my_factory_app
RUN chmod +x run.sh

# 9. สั่งรัน
CMD ["./run.sh"]