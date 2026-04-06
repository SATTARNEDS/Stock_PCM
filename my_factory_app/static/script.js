function prepareModal(button) {
    const id = button.getAttribute('data-id');
    const name = button.getAttribute('data-name');
    const code = button.getAttribute('data-code');
    const category = button.getAttribute('data-category');

    document.getElementById('modalItemId').value = id;
    document.getElementById('modalItemName').innerText = name;
    document.getElementById('modalItemCode').innerText = "รหัส: " + code;
    
    // Logic: โชว์เฉพาะถ้าหมวดหมู่คือ Medicine (หรือ ยา)
    const isMedicine = category.toLowerCase().trim() === 'medicine' || category.includes('ยา');
    
    const remarkSection = document.getElementById('remarkSection');
    const remarkInput = document.getElementById('remarkInput');

    if (isMedicine) {
        remarkSection.style.display = 'block';
        remarkInput.required = true;
    } else {
        remarkSection.style.display = 'none';
        remarkInput.required = false;
        remarkInput.value = '';
    }

    new bootstrap.Modal(document.getElementById('withdrawModal')).show();
}

// ในไฟล์ static/script.js
document.addEventListener("DOMContentLoaded", function() {
    // === ส่วนที่ 1: จัดการกราฟ Dashboard ===
    const ctxCanvas = document.getElementById('deptChart');
    if (ctxCanvas) {
        const labelsData = ctxCanvas.getAttribute('data-labels');
        const valuesData = ctxCanvas.getAttribute('data-values');

        if (labelsData && valuesData) {
            try {
                const labels = JSON.parse(labelsData);
                const values = JSON.parse(valuesData);

                new Chart(ctxCanvas, {
                    type: 'doughnut',
                    data: {
                        labels: labels,
                        datasets: [{
                            data: values,
                            backgroundColor: ['#0d6efd', '#198754', '#ffc107', '#dc3545', '#6610f2', '#fd7e14'],
                            borderWidth: 2
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: { legend: { position: 'bottom' } }
                    }
                });
            } catch (e) {
                console.error("เกิดข้อผิดพลาดในการประมวลผล JSON:", e);
            }
        } else {
            console.error("Data attributes not found on canvas - ตรวจสอบไฟล์ HTML ว่าใส่ data-labels หรือยัง");
        }
    }
});

// === ส่วนที่ 2: ฟังก์ชันเปิด Modal เบิกอุปกรณ์ (แก้เส้นสีแดง) ===
function prepareModal(button) {
    // ดึงค่าจาก data-attributes ที่เราใส่ไว้ในปุ่ม
    const id = button.getAttribute('data-id');
    const name = button.getAttribute('data-name');
    const code = button.getAttribute('data-code');
    const category = button.getAttribute('data-category');

    document.getElementById('modalItemId').value = id;
    document.getElementById('modalItemName').innerText = name;
    document.getElementById('modalItemCode').innerText = "รหัส: " + code;
    
    // แสดงช่อง Remark เฉพาะหมวด Medicine หรือ ยา
    const isMedicine = category.toLowerCase().includes('medicine') || category.includes('ยา');
    const remarkSection = document.getElementById('remarkSection');
    const remarkInput = document.getElementById('remarkInput');

    if (remarkSection && remarkInput) {
        if (isMedicine) {
            remarkSection.style.display = 'block';
            remarkInput.required = true;
        } else {
            remarkSection.style.display = 'none';
            remarkInput.required = false;
            remarkInput.value = '';
        }
    }

    new bootstrap.Modal(document.getElementById('withdrawModal')).show();
}

// Auto Focus login input
document.addEventListener("DOMContentLoaded", function() {
    const empInput = document.getElementById('empIdInput');
    if (empInput) empInput.focus();
});