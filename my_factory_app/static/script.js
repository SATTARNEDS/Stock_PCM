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
document.addEventListener("DOMContentLoaded", function () {
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
document.addEventListener("DOMContentLoaded", function () {
    const empInput = document.getElementById('empIdInput');
    if (empInput) empInput.focus();
});
// ==========================================
// 1. ตัวแปร Global และการตั้งค่าเริ่มต้น
// ==========================================
let currentLogPage = 1;
let currentLogLoc = '';
let idleTime = 0;
const maxIdleTime = 60; // Auto-logout 60 นาที
let monthlyChartObj = null; // ตัวแปรเก็บกราฟรายงาน

function checkAuth(response) {
    if (response.status === 401 || response.statusText === 'Unauthorized') {
        Swal.fire({
            icon: 'warning',
            title: 'Session หมดอายุ',
            text: 'กรุณาเข้าสู่ระบบใหม่อีกครั้ง',
            confirmButtonText: 'ตกลง'
        }).then(() => { window.location.href = '/admin_login'; });
        throw new Error("Unauthorized access");
    }
    return response;
}

document.addEventListener('DOMContentLoaded', function () {
    // --- 1.1 ตั้งค่าปฏิทิน (Flatpickr) ---
    const dateConfig = { locale: "th", dateFormat: "Y-m-d", altInput: true, altFormat: "d/m/Y", allowInput: true };
    flatpickr("#date_picker_add", dateConfig);
    flatpickr("#date_picker_lot", dateConfig);
    flatpickr("#date_picker_expiry", dateConfig);
    flatpickr("#add_lot_date_picker_expiry", dateConfig);
    flatpickr("#edit_date_picker_expiry", dateConfig);

    // --- 1.2 วาดกราฟสัดส่วนการเบิก (Chart.js) หน้า Dashboard ---
    try {
        const ctx = document.getElementById('deptChart');
        if (ctx) {
            const labels = {{ (dept_labels or[]) | tojson | safe }};
const values = {{ (dept_values or[]) | tojson | safe }};
if (labels.length > 0) {
    new Chart(ctx, {
        type: 'bar',
        data: {
            labels: labels,
            datasets: [{
                label: 'จำนวน (ชิ้น)',
                data: values,
                backgroundColor: 'rgba(13, 110, 253, 0.7)',
                borderColor: 'rgba(13, 110, 253, 1)',
                borderWidth: 1,
                borderRadius: 4
            }]
        },
        options: { responsive: true, maintainAspectRatio: false, scales: { y: { beginAtZero: true } } }
    });
}
            }
        } catch (error) { console.error("Chart Error:", error); }

// --- 1.3 เริ่มระบบ Real-time Update ---
setupReportYears();
refreshDashboard();
setInterval(refreshDashboard, 15000); // อัปเดตทุก 15 วินาที

// --- 1.4 ระบบดักจับการเคลื่อนไหว ---
document.addEventListener('mousemove', () => idleTime = 0);
document.addEventListener('keypress', () => idleTime = 0);
setInterval(() => {
    idleTime++;
    if (idleTime >= maxIdleTime) window.location.href = "/admin/logout";
}, 60000);
    });

// ==========================================
// 2. ระบบ Dashboard (Real-time)
// ==========================================
function refreshDashboard() {
    // อัปเดตคนออนไลน์
    fetch('/api/admin/online_count').then(checkAuth).then(res => res.json()).then(data => {
        const el = document.getElementById('onlineCount');
        if (el) el.innerText = data.count || 0;
    }).catch(() => { });

    // อัปเดตตารางสต็อก (ถ้าไม่ได้พิมพ์ค้นหาอยู่)
    const searchInput = document.getElementById('stockSearchInput');
    if (searchInput && searchInput.value === '') loadStock();

    // อัปเดตตารางประวัติเบิก
    if (currentLogPage === 1) updateLogTable(false);

    // อัปเดตตารางขอเบิกใหม่
    updatePendingRequests();
}

function updatePendingRequests() {
    fetch('/api/admin/pending_requests').then(checkAuth).then(res => res.text()).then(html => {
        const tableBody = document.getElementById('pendingTableBody');
        if (tableBody) tableBody.innerHTML = html;

        const hasData = !html.includes("ไม่มีรายการรออนุมัติ");
        const rowCount = hasData ? (html.match(/<tr>/g) || []).length : 0;

        if (document.getElementById('pendingStatCount')) document.getElementById('pendingStatCount').innerText = rowCount;
        if (document.getElementById('pendingBadgeCount')) document.getElementById('pendingBadgeCount').innerText = rowCount + " รายการ";
    });
}

// ==========================================
// 3. ระบบจัดการสินค้า (สต็อก)
// ==========================================
function loadStock() {
    const cat = document.getElementById('stockCatSelector').value;
    const search = document.getElementById('stockSearchInput').value;
    fetch(`/admin/filter_stock?cat=${encodeURIComponent(cat)}&search=${encodeURIComponent(search)}`)
        .then(checkAuth).then(res => res.text()).then(html => {
            document.getElementById('stockTableBody').innerHTML = html;
        });
}

function generateNextCode() {
    const category = document.getElementById('productCategory').value;
    const location = document.getElementById('productLocation').value;
    if (!category || !location) return;
    fetch(`/admin/get_next_code?category=${encodeURIComponent(category)}&location=${encodeURIComponent(location)}`)
        .then(checkAuth).then(res => res.json()).then(data => {
            document.getElementById('productCode').value = data.next_code;
        });
}

function submitAddProduct() {
    const form = document.querySelector('#addProductModal form');
    fetch('/admin/add_product', { method: 'POST', body: new FormData(form) })
        .then(checkAuth).then(res => res.json()).then(data => {
            if (data.success) {
                bootstrap.Modal.getInstance(document.getElementById('addProductModal')).hide();
                form.reset(); loadStock();
                Swal.fire({ icon: 'success', title: 'เพิ่มสำเร็จ', showConfirmButton: false, timer: 1500 });
            } else { Swal.fire({ icon: 'error', title: 'ผิดพลาด', text: data.message }); }
        });
}

function toggleProductStatus(id, name, currentStatus) {
    const actionText = currentStatus === 1 ? 'ปิดการใช้งาน' : 'เปิดการใช้งาน';
    Swal.fire({
        title: `ยืนยัน${actionText}?`, icon: 'warning', showCancelButton: true, confirmButtonText: 'ตกลง', cancelButtonText: 'ยกเลิก'
    }).then((result) => {
        if (result.isConfirmed) {
            fetch(`/admin/toggle_product_status/${id}`, { method: 'POST' })
                .then(checkAuth).then(res => res.json()).then(data => {
                    if (data.success) { loadStock(); Swal.fire('สำเร็จ!', '', 'success'); }
                });
        }
    });
}

function openAddLotModal(code) {
    fetch('/admin/get_product/' + code).then(checkAuth).then(res => res.json()).then(data => {
        document.getElementById('addLot_product_id').value = data.id;
        document.getElementById('addLot_product_display').value = `[${data.code}] ${data.name}`;

        // --- ส่วนที่ดึงกลับมา: สร้างเลข Lot และวันที่อัตโนมัติ ---
        const today = new Date();
        const d = String(today.getDate()).padStart(2, '0');
        const m = String(today.getMonth() + 1).padStart(2, '0');
        const y = today.getFullYear();
        const lotString = `${d}${m}${y}`; // รูปแบบ DDMMYYYY

        // ใส่วันที่ในปฏิทิน
        const datePickerLot = document.getElementById('date_picker_lot');
        if (datePickerLot && datePickerLot._flatpickr) {
            datePickerLot._flatpickr.setDate(today);
        }
        // ใส่เลข Lot ในช่อง
        document.getElementById('addLot_number').value = lotString;
        // --------------------------------------------------

        new bootstrap.Modal(document.getElementById('addLotModal')).show();
    });
}

function submitAddLot() {
    const form = document.getElementById('addLotForm');
    fetch('/admin/add_product_ajax', { method: 'POST', body: new FormData(form) })
        .then(checkAuth).then(res => res.json()).then(data => {
            if (data.success) {
                bootstrap.Modal.getInstance(document.getElementById('addLotModal')).hide();
                form.reset(); loadStock();
                Swal.fire({ icon: 'success', title: 'เพิ่มสต็อกเรียบร้อย!', timer: 1500, showConfirmButton: false });
            }
        });
}

function editProductModal(code) {
    fetch('/admin/get_product/' + code).then(checkAuth).then(res => res.json()).then(data => {
        document.getElementById('edit_code').value = data.code;
        document.getElementById('edit_code_display').value = data.code;
        document.getElementById('edit_name').value = data.name;
        document.getElementById('edit_category').value = data.category;
        document.getElementById('edit_category_display').value = data.category;
        document.getElementById('edit_unit').value = data.unit;
        document.getElementById('edit_location').value = data.location;
        document.getElementById('edit_location_display').value = data.location;
        document.getElementById('edit_safety_stock').value = data.safety_stock;
        document.getElementById('edit_stock').value = data.stock;
        if (document.getElementById('edit_date_picker_expiry')._flatpickr) {
            document.getElementById('edit_date_picker_expiry')._flatpickr.setDate(data.expiry_date || '');
        }
        new bootstrap.Modal(document.getElementById('editProductModal')).show();
    });
}

function submitEditProduct() {
    const form = document.getElementById('editProductForm');
    fetch('/admin/edit_product', { method: 'POST', body: new FormData(form) })
        .then(checkAuth).then(res => res.json()).then(data => {
            if (data.success) {
                bootstrap.Modal.getInstance(document.getElementById('editProductModal')).hide();
                loadStock();
                Swal.fire({ icon: 'success', title: 'แก้ไขสำเร็จ', timer: 1500, showConfirmButton: false });
            }
        });
}

// ==========================================
// 4. ระบบประวัติการเบิก (Logs & Pagination)
// ==========================================
function updateLogTable(shouldScroll = true) {
    fetch(`/admin/filter_logs?page=${currentLogPage}&log_loc=${encodeURIComponent(currentLogLoc)}`)
        .then(checkAuth)
        .then(response => {
            const newTotalPages = response.headers.get('X-Total-Pages');
            if (newTotalPages) document.getElementById('totalPageNum').innerText = newTotalPages;
            return response.text();
        })
        .then(html => {
            document.getElementById('logTableBody').innerHTML = html;
            document.getElementById('currentPageNum').innerText = currentLogPage;
            const totalPages = parseInt(document.getElementById('totalPageNum').innerText);
            document.getElementById('prevPageItem').classList.toggle('disabled', currentLogPage <= 1);
            document.getElementById('nextPageItem').classList.toggle('disabled', currentLogPage >= totalPages);
            if (shouldScroll) document.querySelector('.card-header.bg-primary').scrollIntoView({ behavior: 'smooth', block: 'start' });
        });
}

function filterLogs(loc) {
    currentLogLoc = loc;
    currentLogPage = 1;
    updateLogTable();
    const btnMap = { '': 'btn-log-all', 'PC1': 'btn-log-pc1', 'CC': 'btn-log-cc' };
    Object.keys(btnMap).forEach(key => {
        const btn = document.getElementById(btnMap[key]);
        if (btn) btn.className = (key === loc) ? 'btn btn-sm rounded-pill px-3 btn-primary fw-medium' : 'btn btn-sm rounded-pill px-3 btn-light text-muted border-0 fw-medium';
    });
}

function prevLogPage() { if (currentLogPage > 1) { currentLogPage--; updateLogTable(); } }
function nextLogPage() {
    const totalPages = parseInt(document.getElementById('totalPageNum').innerText);
    if (currentLogPage < totalPages) { currentLogPage++; updateLogTable(); }
}

// ==========================================
// 5. ระบบรายงานประจำเดือน (Report Section)
// ==========================================
function toggleReportSection() {
    const section = document.getElementById('monthlyReportSection');
    if (section.style.display === 'none') {
        section.style.display = 'block';
        section.scrollIntoView({ behavior: 'smooth', block: 'start' });
        setupReportYears();
        fetchMonthlyReport();
    } else {
        section.style.display = 'none';
    }
}

function setupReportYears() {
    const yearSelect = document.getElementById('reportYear');
    if (!yearSelect || yearSelect.options.length > 0) return;
    const currentYear = new Date().getFullYear();
    for (let i = 0; i < 5; i++) {
        let year = currentYear - i;
        yearSelect.innerHTML += `<option value="${year}">${year + 543}</option>`;
    }
}

function fetchMonthlyReport() {
    const month = document.getElementById('reportMonth').value;
    const year = document.getElementById('reportYear').value;
    fetch(`/admin/get_monthly_report_data?month=${month}&year=${year}`)
        .then(checkAuth).then(res => res.json()).then(data => {
            const ctx = document.getElementById('monthlyChart').getContext('2d');
            if (monthlyChartObj) monthlyChartObj.destroy();
            monthlyChartObj = new Chart(ctx, {
                type: 'bar',
                data: {
                    labels: data.labels,
                    datasets: [{
                        label: `รายการสินค้าที่ถูกเบิก ประจำเดือน ${month}/${parseInt(year) + 543}`,
                        data: data.values,
                        backgroundColor: 'rgba(54, 162, 235, 0.7)',
                        borderColor: 'rgba(54, 162, 235, 1)',
                        borderWidth: 1
                    }]
                },
                options: { responsive: true, maintainAspectRatio: false }
            });
        });
}

function exportMonthlyReport() {
    const month = document.getElementById('reportMonth').value;
    const year = document.getElementById('reportYear').value;
    window.location.href = `/admin/export_monthly_excel?month=${month}&year=${year}`;
}

// ==========================================
// 6. ระบบพนักงานค้าง (Zombie) & พนักงานทั้งหมด
// ==========================================
function openZombieModal() {
    fetchZombieUsers();
    new bootstrap.Modal(document.getElementById('zombieModal')).show();
}

function fetchZombieUsers() {
    fetch('/admin/list_zombies_json').then(checkAuth).then(res => res.json()).then(users => {
        const tbody = document.getElementById('zombieTableBody');
        if (users.length === 0) {
            tbody.innerHTML = '<tr><td colspan="5" class="text-center py-4 text-muted">ไม่มีพนักงานค้าง</td></tr>';
            return;
        }
        tbody.innerHTML = users.map(user => `
                <tr>
                    <td class="ps-4">${user.emp_id}</td>
                    <td>${user.name}</td>
                    <td>${user.department}</td>
                    <td>${user.last_seen || '-'}</td>
                    <td class="text-center">
                        <button onclick="unlockZombie('${user.emp_id}')" class="btn btn-sm btn-danger rounded-pill">ปลดล็อก</button>
                    </td>
                </tr>
            `).join('');
    });
}

function unlockZombie(empId) {
    fetch(`/admin/unlock_user_ajax/${empId}`, { method: 'POST' })
        .then(checkAuth).then(res => res.json()).then(data => {
            if (data.success) { fetchZombieUsers(); refreshDashboard(); }
        });
}

function loadUserList() {
    // อัปเดตรายชื่อแผนกใน Dropdown
    fetch('/admin/list_departments').then(checkAuth).then(res => res.json()).then(depts => {
        document.getElementById('userDeptSelect').innerHTML = '<option value="">-- เลือกแผนก --</option>' + depts.map(d => `<option value="${d.name}">${d.name}</option>`).join('');
    });

    // โหลดรายชื่อพนักงาน
    fetch('/admin/list_users').then(checkAuth).then(res => res.json()).then(users => {
        const tbody = document.getElementById('userTableBody');
        tbody.innerHTML = users.map(user => `
                <tr>
                    <td class="ps-4 fw-medium text-primary"># ${user.emp_id}</td>
                    <td class="fw-medium">${user.name}</td>
                    <td>${user.department || 'ไม่ระบุ'}</td>
                    <td class="text-center">${user.location}</td>
                    <td class="text-center">
                        <button class="btn btn-sm btn-light border text-danger" onclick="deleteUser('${user.emp_id}')"><i class="fas fa-trash"></i></button>
                    </td>
                </tr>
            `).join('');
    });
}

function submitAddUser() {
    fetch('/admin/add_user_ajax', { method: 'POST', body: new FormData(document.getElementById('addUserForm')) })
        .then(checkAuth).then(res => res.json()).then(data => {
            if (data.success) { document.getElementById('addUserForm').reset(); loadUserList(); Swal.fire('สำเร็จ', '', 'success'); }
            else { Swal.fire('ผิดพลาด', data.message, 'error'); }
        });
}

function deleteUser(emp_id) {
    Swal.fire({ title: 'ยืนยันลบพนักงาน?', icon: 'warning', showCancelButton: true, confirmButtonText: 'ลบ' }).then((result) => {
        if (result.isConfirmed) {
            fetch(`/admin/delete_user/${emp_id}`, { method: 'POST' }).then(checkAuth).then(res => res.json()).then(data => {
                if (data.success) { loadUserList(); } else { Swal.fire('ผิดพลาด', data.message, 'error'); }
            });
        }
    });
}

// ==========================================
// 7. Utility (ตั้งเวลา, Import, ยืนยัน)
// ==========================================
function confirmAction(url, title, icon = 'question') {
    Swal.fire({
        title: title, icon: icon, showCancelButton: true, confirmButtonText: 'ตกลง', cancelButtonText: 'ยกเลิก'
    }).then((result) => { if (result.isConfirmed) window.location.href = url; });
}

function submitImport() {
    const fileInput = document.getElementById('importFile');
    if (fileInput.files.length > 0) {
        Swal.fire({ title: 'ยืนยันการนำเข้า?', icon: 'question', showCancelButton: true, confirmButtonText: 'ตกลง' }).then((result) => {
            if (result.isConfirmed) {
                Swal.fire({ title: 'กำลังนำเข้า...', didOpen: () => { Swal.showLoading(); } });
                document.getElementById('importForm').submit();
            }
        });
    }
}

function openAlertSettingModal() {
    fetch('/admin/get_alert_time').then(checkAuth).then(res => res.json()).then(data => {
        if (data.time) {
            const [hour, minute] = data.time.split(':');
            document.getElementById('alert_hour').value = hour;
            document.getElementById('alert_minute').value = minute;
        }
        new bootstrap.Modal(document.getElementById('alertSettingModal')).show();
    }).catch(() => new bootstrap.Modal(document.getElementById('alertSettingModal')).show());
}

function saveAlertTime() {
    const formData = new FormData();
    formData.append('alert_time', `${document.getElementById('alert_hour').value}:${document.getElementById('alert_minute').value}`);
    fetch('/admin/save_alert_time', { method: 'POST', body: formData }).then(checkAuth).then(res => res.json()).then(data => {
        if (data.success) {
            bootstrap.Modal.getInstance(document.getElementById('alertSettingModal')).hide();
            Swal.fire('บันทึกสำเร็จ!', '', 'success');
        }
    });
}
// เปิดหน้าต่างและดึงข้อมูลสินค้ามาแสดง
function openWriteOffModal(code) {
    fetch('/admin/get_product/' + code).then(checkAuth).then(res => res.json()).then(data => {
        document.getElementById('writeOff_product_id').value = data.id;
        document.getElementById('writeOff_product_display').value = `[${data.code}] ${data.name}`;
        document.getElementById('writeOff_current_stock').value = data.stock + ' ' + data.unit;
        document.getElementById('writeOff_qty').max = data.stock; // ห้ามตัดเกินสต็อกที่มี
        document.getElementById('writeOff_qty').value = ''; // เคลียร์ค่าเดิม

        new bootstrap.Modal(document.getElementById('writeOffModal')).show();
    });
}

// ส่งข้อมูลไปบันทึก
function submitWriteOff() {
    const qty = document.getElementById('writeOff_qty').value;
    if (!qty || qty <= 0) {
        Swal.fire('แจ้งเตือน', 'กรุณาระบุจำนวนที่ต้องการตัดจำหน่าย', 'warning');
        return;
    }

    Swal.fire({
        title: 'ยืนยันการตัดจำหน่าย?',
        text: "สต็อกจะถูกหักออกและบันทึกลงประวัติทันที",
        icon: 'warning',
        showCancelButton: true,
        confirmButtonColor: '#dc3545',
        confirmButtonText: 'ยืนยันตัดทิ้ง',
        cancelButtonText: 'ยกเลิก'
    }).then((result) => {
        if (result.isConfirmed) {
            const form = document.getElementById('writeOffForm');
            fetch('/admin/write_off_ajax', { method: 'POST', body: new FormData(form) })
                .then(checkAuth).then(res => res.json()).then(data => {
                    if (data.success) {
                        bootstrap.Modal.getInstance(document.getElementById('writeOffModal')).hide();
                        form.reset();
                        loadStock(); // รีเฟรชตารางสต็อก
                        updateLogTable(); // รีเฟรชตารางประวัติเพื่อโชว์รายการตัดทิ้ง
                        Swal.fire({ icon: 'success', title: 'ตัดจำหน่ายเรียบร้อย', timer: 1500, showConfirmButton: false });
                    } else {
                        Swal.fire('ผิดพลาด', data.message, 'error');
                    }
                });
        }
    });
}
// ฟังก์ชันล้างข้อมูลระบบ
function confirmClearData(target) {
    let titleText = target === 'logs' ? 'ล้างประวัติการทำรายการ (Logs)' : 'ล้างสต็อกทั้งหมด (Lots & Stock)';
    let warningText = target === 'logs' ? 'ประวัติทั้งหมดจะถูกลบทิ้งอย่างถาวร ไม่สามารถกู้คืนได้!' : 'สินค้าทุกรายการจะกลายเป็น 0 ชิ้น และล็อตทั้งหมดจะถูกลบทิ้ง!';

    Swal.fire({
        title: '⚠️ ยืนยันการ Set Zero',
        html: `<div class="text-danger fw-medium mb-3">${warningText}</div><div class="text-muted small">โปรดใส่รหัสผ่านพิเศษ (Secondary Password) เพื่อยืนยันคำสั่งนี้</div>`,
        icon: 'warning',
        input: 'password', // กำหนดให้ Popup มีช่องใส่รหัสผ่าน (ซ่อนตัวอักษร)
        inputPlaceholder: 'ใส่รหัสผ่านพิเศษที่นี่...',
        inputAttributes: {
            autocapitalize: 'off',
            autocorrect: 'off'
        },
        showCancelButton: true,
        confirmButtonColor: '#dc3545',
        cancelButtonColor: '#6c757d',
        confirmButtonText: '<i class="fas fa-radiation me-2"></i>ยืนยันล้างข้อมูล',
        cancelButtonText: 'ยกเลิก',
        preConfirm: (password) => {
            if (!password) {
                Swal.showValidationMessage('กรุณาใส่รหัสผ่านเพื่อยืนยันสิทธิ์');
            }
            return password;
        }
    }).then((result) => {
        if (result.isConfirmed) {
            // โชว์โหลดดิ้งระหว่างล้างข้อมูล
            Swal.fire({ title: 'กำลังล้างข้อมูล...', allowOutsideClick: false, didOpen: () => { Swal.showLoading(); } });

            // ส่งข้อมูลไปหลังบ้าน
            let formData = new FormData();
            formData.append('target', target);
            formData.append('password', result.value);

            fetch('/admin/clear_system_data', { method: 'POST', body: formData })
                .then(checkAuth).then(res => res.json()).then(data => {
                    if (data.success) {
                        Swal.fire({
                            icon: 'success',
                            title: 'ล้างข้อมูลสำเร็จ!',
                            text: 'ระบบได้รับการ Set Zero เรียบร้อยแล้ว',
                            timer: 2000,
                            showConfirmButton: false
                        }).then(() => {
                            location.reload(); // รีเฟรชหน้าเว็บอัตโนมัติ
                        });
                    } else {
                        Swal.fire('ผิดพลาด', data.message, 'error');
                    }
                }).catch(() => Swal.fire('ผิดพลาด', 'ไม่สามารถเชื่อมต่อเซิร์ฟเวอร์ได้', 'error'));
        }
    });
}
