# 🏥 เอกสารสรุปการทำงานระบบ Stock_PCM

> เวอร์ชันนี้จัดรูปแบบให้อ่านง่ายขึ้น เหมาะสำหรับใช้ส่งงาน อธิบายระบบ หรือแนบประกอบโปรเจกต์

ระบบ Stock_PCM เป็นเว็บไซต์สำหรับจัดการการเบิกจ่ายสินค้าและยา โดยรองรับทั้งฝั่ง **CC** และ **PC1** พร้อมระบบ **FIFO**, **Admin Approval**, และ **LINE Notification** แยกตามกลุ่มงาน

---

## 1. วัตถุประสงค์ของระบบ

ระบบนี้ถูกออกแบบมาเพื่อให้สามารถ:
- จัดการการเบิกสินค้าและยาได้จากหน้าเว็บ
- แยกสิทธิ์การทำงานตาม location ของผู้ใช้
- รองรับการเบิกยาแบบแตกหน่วย เช่น เบิกเป็น **เม็ด** แต่เก็บเป็น **ห่อ / แผง / ขวด**
- ใช้หลัก **FIFO** ในการจ่ายยา
- ให้ Admin อนุมัติรายการก่อนสรุปผล
- แจ้งเตือนผ่าน LINE แยกเป็น 2 กลุ่ม คือ **CC** และ **PC1**

---

## 2. ภาพรวมการทำงานของเว็บไซต์

```mermaid
flowchart LR
    subgraph U[ส่วนผู้ใช้งาน]
        A[ผู้ใช้ Login]
        B[หน้าเมนูสินค้า]
        C[ค้นหา / เลือกสินค้า]
        D[เพิ่มเข้าตะกร้า]
        E[ยืนยันการเบิก]
        A --> B --> C --> D --> E
    end

    subgraph S[ส่วนประมวลผลของระบบ]
        F{สินค้าเป็นยาแตกหน่วยหรือไม่}
        G[คำนวณ FIFO และตัดจาก open package ก่อน]
        H[ใช้ flow สินค้าทั่วไป]
        I[สร้าง log คำขอเบิก]
        E --> F
        F -- ใช่ --> G --> I
        F -- ไม่ใช่ --> H --> I
    end

    subgraph A1[ส่วนการอนุมัติและแจ้งเตือน]
        J[ส่ง LINE แจ้งคำขอ]
        K[Admin Dashboard]
        L[อนุมัติ / ปฏิเสธ]
        M[อัปเดต stock / lot / log]
        N[ส่ง LINE แจ้งผลอนุมัติ]
        I --> J --> K --> L --> M --> N
    end

    classDef user fill:#E3F2FD,stroke:#1E88E5,color:#0D47A1,stroke-width:2px;
    classDef system fill:#E8F5E9,stroke:#43A047,color:#1B5E20,stroke-width:2px;
    classDef admin fill:#FFF3E0,stroke:#FB8C00,color:#E65100,stroke-width:2px;

    class A,B,C,D,E user;
    class F,G,H,I system;
    class J,K,L,M,N admin;
```

### ใจความสำคัญ
- ผู้ใช้เริ่มจาก login ด้วยรหัสพนักงาน
- ระบบกรองสินค้าให้ตามฝั่ง **CC** หรือ **PC1**
- เมื่อเบิกยา ระบบจะคำนวณ FIFO โดยอัตโนมัติ
- เมื่อ Admin อนุมัติ ระบบจะอัปเดตฐานข้อมูลและส่ง LINE แจ้งผล

---

## 3. การแยกการทำงานตามฝั่ง CC และ PC1

```mermaid
flowchart TD
    A[ผู้ใช้เข้าสู่ระบบ] --> B{ตรวจ location}

    subgraph CC[ฝั่ง CC]
        C[แสดงสินค้า CC + General]
        E[ส่ง LINE ไปกลุ่ม CC]
        C --> E
    end

    subgraph PC1[ฝั่ง PC1]
        D[แสดงสินค้า PC1 + General]
        F[ส่ง LINE ไปกลุ่ม PC1]
        D --> F
    end

    B -- CC / Coil Center --> C
    B -- PC1 --> D

    classDef decision fill:#F3E5F5,stroke:#8E24AA,color:#4A148C,stroke-width:2px;
    classDef cc fill:#E8F5E9,stroke:#2E7D32,color:#1B5E20,stroke-width:2px;
    classDef pc1 fill:#E1F5FE,stroke:#039BE5,color:#01579B,stroke-width:2px;

    class B decision;
    class C,E cc;
    class D,F pc1;
```

### หลักการแยกฝั่ง
- ถ้าผู้ใช้มาจาก **CC** → เห็นสินค้า CC และแจ้งเตือน LINE ไปกลุ่ม CC
- ถ้าผู้ใช้มาจาก **PC1** → เห็นสินค้า PC1 และแจ้งเตือน LINE ไปกลุ่ม PC1
- Admin แต่ละฝั่งอนุมัติได้เฉพาะรายการของฝั่งตัวเอง

---

## 4. Flow การเบิกสินค้าทั่วไป

การเบิกของทั่วไปมีลำดับดังนี้

1. ผู้ใช้เลือกสินค้า
2. ระบบเพิ่มรายการลงตะกร้า
3. stock ถูกลดทันที
4. reserved_stock ถูกเพิ่ม
5. ผู้ใช้กดยืนยันการเบิก
6. ระบบสร้างรายการรออนุมัติใน log
7. ส่ง LINE แจ้ง Admin
8. เมื่อ Admin อนุมัติ รายการจะเปลี่ยนเป็น **Approved**

---

## 5. Flow การเบิกยาแบบ FIFO

### 5.1 เงื่อนไขที่ระบบมองว่าเป็นยาแตกหน่วย
สินค้าจะเข้าสู่ flow นี้เมื่อ:
- อยู่ในหมวด **ยา**
- มีค่า **conversion_rate > 1**
- หน่วยแพ็กเป็นลักษณะ เช่น **ห่อ, แผง, ซอง, ขวด, กล่อง, กระปุก**

### 5.2 ผังการคำนวณ FIFO

```mermaid
flowchart TD
    subgraph INPUT[ขั้นตอนจากผู้ใช้]
        A[ผู้ใช้เลือกยา]
        B[ใส่จำนวนที่ต้องการเบิก]
        C{ระบุเป็นเม็ดหรือเป็นแพ็ก}
        A --> B --> C
    end

    subgraph CALC[ขั้นตอนคำนวณของระบบ]
        D[ใช้ base unit]
        E[แปลงเป็น base unit]
        F[ตรวจ stock รวม]
        G{ของพอหรือไม่}
        I[ใช้ open_packages ก่อน]
        J{ยังไม่พอหรือไม่}
        K[เปิดแพ็กใหม่]
        L[ใช้ของจากแพ็กที่เปิดอยู่]
        M[บันทึก open_packages ใหม่]
        N[บันทึก transaction_logs]
    end

    O[รอ Admin อนุมัติ]
    H[แจ้งเตือน ของไม่พอ]

    C -- เม็ด --> D --> F
    C -- แพ็ก --> E --> F
    F --> G
    G -- ไม่พอ --> H
    G -- พอ --> I --> J
    J -- ใช่ --> K --> M --> N --> O
    J -- ไม่ใช่ --> L --> N

    classDef input fill:#E3F2FD,stroke:#1565C0,color:#0D47A1,stroke-width:2px;
    classDef process fill:#E8F5E9,stroke:#2E7D32,color:#1B5E20,stroke-width:2px;
    classDef warn fill:#FFEBEE,stroke:#E53935,color:#B71C1C,stroke-width:2px;
    classDef wait fill:#FFF8E1,stroke:#F9A825,color:#F57F17,stroke-width:2px;

    class A,B,C input;
    class D,E,F,G,I,J,K,L,M,N process;
    class H warn;
    class O wait;
```

### 5.3 หลัก FIFO ที่ระบบใช้จริง

#### ชั้นที่ 1: open_packages
- ใช้ของจากแพ็กที่เปิดอยู่ก่อน
- เลือกจากรายการที่เปิดไว้เก่าที่สุด

#### ชั้นที่ 2: product_lots
- ตอน Admin อนุมัติ ระบบจะตัด lot แบบ FIFO
- เรียงตาม received_date จากเก่าไปใหม่

---

## 6. ตัวอย่างจริง: มายบาซิน

### ข้อมูลสินค้า
- ชื่อสินค้า: **Throat Lozenge / ยาอมแก้เจ็บคอ มายบาซิน**
- หน่วยแพ็ก: **ห่อ**
- หน่วยย่อย: **เม็ด**
- อัตราแปลง: **1 ห่อ = 70 เม็ด**

### ตัวอย่างสถานะก่อนเบิก
- stock แบบห่อ = 0
- open package เหลือ = 60 เม็ด

### ผู้ใช้เบิก
- เบิก **10 เม็ด**

### ผลลัพธ์ที่เกิดขึ้น
- ระบบใช้ของจากห่อที่เปิดอยู่ก่อน
- จำนวนใน open package เปลี่ยนจาก **60 → 50 เม็ด**
- ไม่ต้องเปิดห่อใหม่

### ตัวอย่างข้อความ LINE
- **จำนวน: 10 เม็ด**
- **เบิกจากห่อที่เปิดแล้ว 10 เม็ด**

---

## 7. Flow การอนุมัติของ Admin

```mermaid
flowchart LR
    subgraph ADMIN[กระบวนการของ Admin]
        A[รายการรออนุมัติ]
        B[Admin CC / Admin PC1]
        C{ตรวจสิทธิ์ตามฝั่ง}
        E[ตรวจ log ที่ Pending]
        F[ตัด lot ตาม FIFO]
        G[อัปเดต transaction_logs เป็น Approved]
        H[เพิ่มค่า withdraw]
        I[เช็ก safety stock]
        J[ส่ง LINE แจ้งว่า Admin ยืนยันแล้ว]
        A --> B --> C
        C -- ผ่าน --> E --> F --> G --> H --> I --> J
    end

    D[ปฏิเสธการเข้าถึง]
    C -- ไม่ผ่าน --> D

    classDef admin fill:#FFF3E0,stroke:#EF6C00,color:#E65100,stroke-width:2px;
    classDef reject fill:#FFEBEE,stroke:#C62828,color:#B71C1C,stroke-width:2px;

    class A,B,C,E,F,G,H,I,J admin;
    class D reject;
```

### สิ่งที่ระบบทำเมื่อ Admin กดอนุมัติ
- เปลี่ยนสถานะรายการเป็น **Approved**
- บันทึกเวลาอนุมัติ
- อัปเดต lot ที่ถูกใช้
- เพิ่มค่า withdraw ของสินค้า
- ตรวจสอบว่า stock ต่ำกว่า safety stock หรือไม่
- ส่ง LINE แจ้งผลกลับไปยังกลุ่มที่ถูกต้อง

---

## 8. ระบบแจ้งเตือน LINE

ระบบส่งข้อความหลัก 3 ช่วง

1. **ตอนผู้ใช้ส่งคำขอเบิก**
2. **ตอน stock ต่ำกว่า safety stock**
3. **ตอน Admin ยืนยันรายการแล้ว**

### Mermaid แสดงการ routing ของ LINE

```mermaid
flowchart TD
    A[เกิด event ในระบบ] --> B{event อะไร}
    B -- ส่งคำขอเบิก --> C[แจ้ง Admin]
    B -- stock ต่ำ --> D[แจ้งเตือนสั่งซื้อ]
    B -- Admin อนุมัติ --> E[แจ้งผลการอนุมัติ]

    C --> F{ฝั่งไหน}
    D --> F
    E --> F

    subgraph LINE_CC[กลุ่ม LINE ฝั่ง CC]
        G[CC Notification]
    end

    subgraph LINE_PC1[กลุ่ม LINE ฝั่ง PC1]
        H[PC1 Notification]
    end

    F -- CC --> G
    F -- PC1 --> H

    classDef event fill:#EDE7F6,stroke:#5E35B1,color:#311B92,stroke-width:2px;
    classDef linecc fill:#E8F5E9,stroke:#2E7D32,color:#1B5E20,stroke-width:2px;
    classDef linepc1 fill:#E1F5FE,stroke:#0277BD,color:#01579B,stroke-width:2px;

    class A,B,C,D,E,F event;
    class G linecc;
    class H linepc1;
```

---

## 9. โครงสร้างตารางหลักในฐานข้อมูล

### users
- ข้อมูลพนักงาน
- department
- location
- สถานะ lock การใช้งาน

### products
- stock
- reserved_stock
- withdraw
- base_unit
- package_unit
- conversion_rate
- location

### carts
- รายการที่ผู้ใช้เลือกก่อนยืนยัน

### product_lots
- เก็บข้อมูล lot เพื่อใช้ FIFO

### open_packages
- เก็บจำนวนคงเหลือของแพ็กที่ถูกเปิดแล้ว

### transaction_logs
- request log
- approved log
- medicine audit log

---

## 10. สรุปความสามารถของระบบในปัจจุบัน

✅ รองรับทั้ง **CC** และ **PC1**  
✅ แจ้งเตือน LINE แยก 2 กลุ่มได้แล้ว  
✅ เบิกยาแบบแตกหน่วยได้ เช่น **เม็ด**  
✅ มีการบังคับกรอกอาการเมื่อเบิกยา  
✅ FIFO ทำงานผ่านทั้ง **open_packages** และ **product_lots**  
✅ Admin อนุมัติแล้วมี LINE แจ้งกลับ  
✅ ข้อความ LINE แสดงหน่วยจริงได้ถูกต้อง เช่น **10 เม็ด**

