"""
Unit Conversion Module for Pharmaceutical Inventory
ห้องยา: Support เบิกยาเป็นเม็ด แต่จัดเก็บเป็น ขวด/แผง/กระปุก

Example:
  - Aspirin: 1 bottle = 20 tablets, current stock = 5 bottles = 100 tablets
  - User wants to withdraw 23 tablets
  - System should:
    1. Calculate: need 2 bottles (1 full + 1 opened)
    2. Track: 1 bottle goes to open_packages with 17 tablets remaining
    3. Update: stock becomes 3 bottles + 1 opened bottle with 17 tablets
"""

from decimal import Decimal
import sqlite3


class UnitConversionManager:
    """Manages unit conversions for multi-unit pharmacy inventory"""
    
    def __init__(self, db_connection):
        self.conn = db_connection
        # Set row_factory BEFORE any cursor is created
        self.conn.row_factory = sqlite3.Row
        # Verify it takes effect
        test_cursor = self.conn.cursor()
        test_cursor.close()
        self.cursor = self.conn.cursor()
    
    # ============================================================================
    # 1. GET PRODUCT UNIT INFO
    # ============================================================================
    
    def get_product_unit_info(self, product_id):
        """
        Get unit conversion info for a product
        
        Returns: {
            'base_unit': 'tablet',
            'package_unit': 'bottle', 
            'conversion_rate': 20,
            'stock_base_unit': 100,  # Total in base units
            'stock_package_unit': 5,  # Total in package units
            'has_open_box': True,
            'open_box_qty': 15  # Remaining tablets in opened bottle
        }
        """
        # Get product info
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM products WHERE id = ?', (product_id,))
        cols = cursor.description
        row = cursor.fetchone()
        
        if not row:
            raise ValueError(f"Product {product_id} not found")
        
        # Create dict from row and column names
        col_names = [col[0] for col in cols]
        product = {col_names[i]: row[i] for i in range(len(col_names))}
        
        # Get count of open boxes
        cursor.execute('''
            SELECT SUM(base_unit_qty) as total,
                   SUM(extra_tablet_qty) as extra_total
            FROM open_packages
            WHERE product_id = ? AND status = 'active'
        ''', (product_id,))
        open_box_row = cursor.fetchone()
        open_box = {
            'total': open_box_row[0] if open_box_row else 0,
            'extra_total': open_box_row[1] if open_box_row else 0,
        }
        
        # Extract values safely
        base_unit = product.get('base_unit') or 'tablet'
        package_unit = product.get('package_unit') or product.get('unit') or 'box'
        conversion_rate = float(product.get('conversion_rate') or 1)
        package_tablet_total = int(product.get('package_tablet_total') or 0)
        base_unit_to_tablet_rate = int(product.get('base_unit_to_tablet_rate') or 0)
        stock_package_unit = product.get('stock') or 0
        open_box_qty = (open_box['total'] or 0) if open_box['total'] else 0
        open_extra_tablet_qty = (open_box['extra_total'] or 0) if open_box['extra_total'] else 0

        if package_tablet_total <= 0 and base_unit_to_tablet_rate > 0:
            package_tablet_total = int(conversion_rate) * base_unit_to_tablet_rate

        per_package_extra_tablets = 0
        if package_tablet_total > 0 and base_unit_to_tablet_rate > 0:
            per_package_extra_tablets = max(0, package_tablet_total - (int(conversion_rate) * base_unit_to_tablet_rate))

        package_remainder_tablets_total = int(stock_package_unit or 0) * int(per_package_extra_tablets or 0)
        pooled_extra_tablet_qty = int(open_extra_tablet_qty) + package_remainder_tablets_total

        open_extra_base_equivalent = 0
        open_extra_tablet_remainder = int(pooled_extra_tablet_qty)
        if base_unit_to_tablet_rate > 0:
            open_extra_base_equivalent = int(pooled_extra_tablet_qty) // int(base_unit_to_tablet_rate)
            open_extra_tablet_remainder = int(pooled_extra_tablet_qty) % int(base_unit_to_tablet_rate)
        
        # Convert to base units
        stock_base_unit = (stock_package_unit * conversion_rate) + open_box_qty + open_extra_base_equivalent
        
        return {
            'product_id': product_id,
            'name': product['name'],
            'code': product['code'],
            'base_unit': base_unit,
            'package_unit': package_unit,
            'conversion_rate': conversion_rate,
            'package_tablet_total': package_tablet_total,
            'base_unit_to_tablet_rate': base_unit_to_tablet_rate,
            'per_package_extra_tablets': per_package_extra_tablets,
            'package_remainder_tablets_total': int(package_remainder_tablets_total),
            'pooled_extra_tablet_qty': int(pooled_extra_tablet_qty),
            'stock_base_unit': int(stock_base_unit),
            'stock_package_unit': stock_package_unit,
            'open_box_qty': int(open_box_qty),
            'open_extra_tablet_qty': int(open_extra_tablet_qty),
            'open_extra_base_equivalent': int(open_extra_base_equivalent),
            'open_extra_tablet_remainder': int(open_extra_tablet_remainder),
            'has_open_box': (open_box_qty > 0) or (open_extra_tablet_qty > 0)
        }
    
    # ============================================
    
    # ============================================================================
    # 2. CONVERT UNITS
    # ============================================================================
    
    def convert_base_to_package(self, product_id, qty_base_unit):
        """
        Convert qty in base_unit to package_unit
        
        Example: Convert 23 tablets to bottles
          - 23 tablets / 20 per bottle = 1.15 bottles
          - Returns: (1 full_packages, 3 remaining_base_units)
        
        Returns: (qty_full_packages, qty_remaining_base_units)
        """
        info = self.get_product_unit_info(product_id)
        conversion_rate = info['conversion_rate']
        
        full_packages = qty_base_unit // conversion_rate
        remaining_base = qty_base_unit % conversion_rate
        
        return int(full_packages), int(remaining_base)
    
    def convert_package_to_base(self, product_id, qty_package_unit):
        """
        Convert qty in package_unit to base_unit
        
        Example: Convert 5 bottles to tablets
          - 5 bottles * 20 per bottle = 100 tablets
        
        Returns: qty_base_unit
        """
        info = self.get_product_unit_info(product_id)
        conversion_rate = info['conversion_rate']
        
        return int(qty_package_unit * conversion_rate)
    
    # ============================================================================
    # 3. CHECK STOCK AVAILABILITY
    # ============================================================================
    
    def check_stock_available(self, product_id, qty_base_unit):
        """
        Check if enough stock available in base_unit
        
        Returns: {
            'available': True/False,
            'stock_base_unit': int,
            'requested_qty': int,
            'message': str
        }
        """
        info = self.get_product_unit_info(product_id)
        stock = info['stock_base_unit']
        
        return {
            'available': stock >= qty_base_unit,
            'stock_base_unit': stock,
            'requested_qty': qty_base_unit,
            'shortage': max(0, qty_base_unit - stock),
            'message': f"Available: {stock} {info['base_unit']}s, Requested: {qty_base_unit}"
        }
    
    # ============================================================================
    # 4. CALCULATE WITHDRAWAL (หลัก)
    # ============================================================================
    
    def calculate_withdrawal(self, product_id, qty_base_unit, use_open_box=True):
        """
        Calculate how many packages + base units need to be withdrawn
        
        Logic:
          1. If open_box exists and use_open_box=True, use from open_box first
          2. Then deduct from full packages
          3. If remainder > 0, must open a new package
        
        Returns: {
            'can_fulfill': True/False,
            'from_open_box': int,  # base_units taken from open box
            'full_packages_needed': int,  # complete packages to take
            'new_open_box_qty': int,  # base_units in newly opened package
            'new_open_box_id': int or None,  # which open_box ID
            'total_packages_used': float,  # e.g., 2.15 means 2 full + 1 partial
            'transaction_note': str
        }
        """
        info = self.get_product_unit_info(product_id)

        base_to_tablet_rate = int(info.get('base_unit_to_tablet_rate') or 0)
        package_tablet_total = int(info.get('package_tablet_total') or 0)

        # โหมดหลายชั้น: รวมเศษจากทุกแพ็คทันที (คำนวณด้วยเม็ดรวม)
        if base_to_tablet_rate > 0 and package_tablet_total > 0:
            requested_tablets = int(qty_base_unit) * base_to_tablet_rate
            open_box_tablets = int(info.get('open_box_qty') or 0) * base_to_tablet_rate
            open_extra_tablets = int(info.get('open_extra_tablet_qty') or 0)
            available_tablets = (int(info.get('stock_package_unit') or 0) * package_tablet_total) + open_box_tablets + open_extra_tablets

            if available_tablets < requested_tablets:
                shortage_tablets = requested_tablets - available_tablets
                shortage_base = (shortage_tablets + base_to_tablet_rate - 1) // base_to_tablet_rate
                return {
                    'can_fulfill': False,
                    'message': f"❌ ของไม่พอ: เหลือ {available_tablets} เม็ด, ต้องการ {requested_tablets} เม็ด",
                    'shortage': int(shortage_base)
                }

            qty_remaining_tablets = requested_tablets
            from_open_box = 0
            from_open_extra_base = 0
            from_open_extra_tablets = 0

            if use_open_box and open_box_tablets > 0:
                take_open_box_tablets = min(open_box_tablets, qty_remaining_tablets)
                from_open_box = take_open_box_tablets // base_to_tablet_rate
                qty_remaining_tablets -= take_open_box_tablets

            if qty_remaining_tablets > 0 and open_extra_tablets > 0:
                from_open_extra_tablets = min(open_extra_tablets, qty_remaining_tablets)
                from_open_extra_base = from_open_extra_tablets // base_to_tablet_rate
                qty_remaining_tablets -= from_open_extra_tablets

            full_packages_needed = 0
            new_open_box_qty = 0
            new_open_extra_tablets = 0
            if qty_remaining_tablets > 0:
                full_packages_needed = (qty_remaining_tablets + package_tablet_total - 1) // package_tablet_total
                if full_packages_needed > int(info.get('stock_package_unit') or 0):
                    return {
                        'can_fulfill': False,
                        'message': '❌ สต็อกแพ็กไม่พอสำหรับการตัดจ่าย',
                        'shortage': int(full_packages_needed - int(info.get('stock_package_unit') or 0))
                    }
                tablets_from_packages = full_packages_needed * package_tablet_total
                leftover_tablets = tablets_from_packages - qty_remaining_tablets
                new_open_box_qty = leftover_tablets // base_to_tablet_rate
                new_open_extra_tablets = leftover_tablets % base_to_tablet_rate

            total_packages_used = float(full_packages_needed)
            if float(info.get('conversion_rate') or 0) > 0:
                total_packages_used += (from_open_box + from_open_extra_base) / float(info['conversion_rate'])

            package_unit = info['package_unit']
            note_parts = []
            if from_open_box > 0:
                note_parts.append(f"เบิกจาก{package_unit}ที่เปิดแล้ว {from_open_box} {info['base_unit']}")
            if from_open_extra_tablets > 0:
                note_parts.append(f"ใช้เศษเม็ดที่เปิดแล้ว {from_open_extra_tablets} เม็ด")
            if full_packages_needed > 0:
                if (new_open_box_qty > 0) or (new_open_extra_tablets > 0):
                    full_closed_packages = max(0, full_packages_needed - 1)
                    remain_note = []
                    if new_open_box_qty > 0:
                        remain_note.append(f"{new_open_box_qty} {info['base_unit']}")
                    if new_open_extra_tablets > 0:
                        remain_note.append(f"เศษ {new_open_extra_tablets} เม็ด")
                    remain_text = ' + '.join(remain_note) if remain_note else '0'
                    if full_closed_packages > 0:
                        note_parts.append(f"เบิก {full_closed_packages} {package_unit}เต็ม + เปิดใหม่ 1 {package_unit} (เหลือ {remain_text})")
                    else:
                        note_parts.append(f"เปิดใหม่ 1 {package_unit} (เหลือ {remain_text})")
                else:
                    note_parts.append(f"เบิก {full_packages_needed} {package_unit}เต็ม")

            return {
                'can_fulfill': True,
                'multi_mode': True,
                'from_open_box': int(from_open_box),
                'from_open_extra_base': int(from_open_extra_base),
                'from_open_extra_tablets': int(from_open_extra_tablets),
                'full_packages_needed': int(full_packages_needed),
                'new_open_box_qty': int(new_open_box_qty),
                'new_open_extra_tablets': int(new_open_extra_tablets),
                'open_box_id': None,
                'total_packages_used': float(total_packages_used),
                'transaction_note': ' + '.join(note_parts),
                'message': f"✅ เบิก {qty_base_unit} {info['base_unit']} ({requested_tablets} เม็ด)"
            }
        
        # Check stock first
        check = self.check_stock_available(product_id, qty_base_unit)
        if not check['available']:
            return {
                'can_fulfill': False,
                'message': f"❌ ของไม่พอ: เหลือ {check['stock_base_unit']} {info['base_unit']}, ต้องการ {qty_base_unit}",
                'shortage': check['shortage']
            }
        
        # Strategy: Use open box first, then full packages
        qty_remaining = qty_base_unit
        from_open_box = 0
        from_open_extra_base = 0
        full_packages_needed = 0
        new_open_box_qty = 0
        open_box_id = None
        
        # 1. Try to use existing open box
        if use_open_box and info['has_open_box']:
            open_box = self.cursor.execute('''
                SELECT id, base_unit_qty FROM open_packages
                WHERE product_id = ? AND status = 'active'
                ORDER BY opened_date ASC LIMIT 1
            ''', (product_id,)).fetchone()
            
            if open_box:
                open_box_id = open_box['id']
                take_from_open = min(open_box['base_unit_qty'], qty_remaining)
                from_open_box = take_from_open
                qty_remaining -= take_from_open

        # 1.5 ใช้เศษเม็ดจากแพ็คที่เปิดแล้วก่อน หากประกอบเป็นหน่วยย่อยได้
        if qty_remaining > 0 and int(info.get('open_extra_base_equivalent') or 0) > 0:
            take_from_open_extra = min(int(info.get('open_extra_base_equivalent') or 0), qty_remaining)
            from_open_extra_base = take_from_open_extra
            qty_remaining -= take_from_open_extra
        
        # 2. If still need more, deduct from full packages
        if qty_remaining > 0:
            full_packages, remaining_base = self.convert_base_to_package(
                product_id, qty_remaining
            )
            
            full_packages_needed = full_packages
            if remaining_base > 0:
                # Must open a new package
                full_packages_needed += 1
                new_open_box_qty = info['conversion_rate'] - remaining_base
        
        # Calculate total packages used
        total_packages_used = full_packages_needed
        if from_open_box > 0:
            total_packages_used += from_open_box / info['conversion_rate']
        if from_open_extra_base > 0:
            total_packages_used += from_open_extra_base / info['conversion_rate']
        
        # Create transaction note
        note_parts = []
        package_unit = info['package_unit']
        if from_open_box > 0:
            note_parts.append(f"เบิกจาก{package_unit}ที่เปิดแล้ว {from_open_box} {info['base_unit']}")
        if from_open_extra_base > 0 and int(info.get('base_unit_to_tablet_rate') or 0) > 0:
            note_parts.append(
                f"ใช้เศษเม็ดที่เปิดแล้ว {from_open_extra_base * int(info.get('base_unit_to_tablet_rate') or 0)} เม็ด "
                f"= {from_open_extra_base} {info['base_unit']}"
            )
        if full_packages_needed > 0:
            if new_open_box_qty > 0:
                full_closed_packages = max(0, full_packages_needed - 1)
                if full_closed_packages > 0:
                    note_parts.append(f"เบิก {full_closed_packages} {package_unit}เต็ม + เปิดใหม่ 1 {package_unit} (เหลือ {new_open_box_qty} {info['base_unit']})")
                else:
                    note_parts.append(f"เปิดใหม่ 1 {package_unit} (เหลือ {new_open_box_qty} {info['base_unit']})")
            else:
                note_parts.append(f"เบิก {full_packages_needed} {package_unit}เต็ม")
        
        return {
            'can_fulfill': True,
            'from_open_box': from_open_box,
            'from_open_extra_base': from_open_extra_base,
            'full_packages_needed': full_packages_needed,
            'new_open_box_qty': new_open_box_qty,
            'open_box_id': open_box_id,
            'total_packages_used': total_packages_used,
            'transaction_note': ' + '.join(note_parts),
            'message': f"✅ เบิก {qty_base_unit} {info['base_unit']} = {total_packages_used:.2f} {info['package_unit']}"
        }
    
    # ============================================================================
    # 5. APPLY WITHDRAWAL (บันทึกลงฐานข้อมูล)
    # ============================================================================
    
    def apply_withdrawal(self, product_id, qty_base_unit, emp_id, lot_id=None, use_open_box=True, autocommit=True):
        """
        Execute the withdrawal and update inventory
        
        Steps:
          1. Calculate what to withdraw
          2. Reduce full packages
          3. Update open_boxes table
          4. Create new open_package if needed
          5. Log transaction
        """
        # Calculate
        calc = self.calculate_withdrawal(product_id, qty_base_unit, use_open_box)
        
        if not calc['can_fulfill']:
            return calc
        
        try:
            # Get product info
            info = self.get_product_unit_info(product_id)
            
            # 1. Reduce stock (full packages) แบบมีเงื่อนไขกันยอดติดลบ
            stock_update = self.cursor.execute('''
                UPDATE products 
                SET stock = stock - ?
                WHERE id = ? AND stock >= ?
            ''', (calc['full_packages_needed'], product_id, calc['full_packages_needed']))

            if stock_update.rowcount == 0:
                raise ValueError('สต็อกแพ็กไม่พอสำหรับการตัดจ่าย')
            
            # 2. If taking from open_box, reduce it
            if calc['from_open_box'] > 0:
                self._consume_open_base_units(product_id, int(calc['from_open_box']))

            # 2.5 ใช้เศษเม็ดจากแพ็คที่เปิดแล้ว (แปลงเป็นหน่วยย่อย)
            if calc.get('from_open_extra_base', 0) > 0 and int(info.get('base_unit_to_tablet_rate') or 0) > 0:
                consume_tablets = int(calc.get('from_open_extra_tablets') or (int(calc['from_open_extra_base']) * int(info.get('base_unit_to_tablet_rate') or 0)))
                self._consume_open_extra_tablets(product_id, consume_tablets)
            
            # 3. Create new open_package if needed
            if calc['new_open_box_qty'] > 0:
                new_open_extra_tablets = int(calc.get('new_open_extra_tablets') or int(info.get('per_package_extra_tablets') or 0))
                self.cursor.execute('''
                    INSERT INTO open_packages 
                    (product_id, lot_id, opened_date, base_unit_qty, extra_tablet_qty, package_unit_qty_before, status)
                    VALUES (?, ?, datetime('now'), ?, ?, 1, 'active')
                ''', (product_id, lot_id, calc['new_open_box_qty'], new_open_extra_tablets))
            
            # 4. Log transaction
            self.cursor.execute('''
                INSERT INTO transaction_logs 
                (emp_id, product_id, lot_id, action, qty, qty_base_unit, qty_package_unit, note, status, timestamp)
                VALUES (?, ?, ?, 'withdraw', ?, ?, ?, ?, 'Approved', datetime('now'))
            ''', (
                emp_id, 
                product_id, 
                lot_id,
                calc['full_packages_needed'],
                qty_base_unit,
                calc['total_packages_used'],
                calc['transaction_note']
            ))
            
            if autocommit:
                self.conn.commit()
            
            return {
                'success': True,
                'message': calc['message'] + ' ✅ บันทึกสำเร็จ',
                'transaction_note': calc['transaction_note'],
                'full_packages_needed': calc['full_packages_needed'],
                'total_packages_used': calc['total_packages_used'],
                'stock_remaining': info['stock_package_unit'] - calc['full_packages_needed']
            }
            
        except Exception as e:
            self.conn.rollback()
            return {
                'success': False,
                'message': f"❌ บันทึกล้มเหลว: {str(e)}"
            }

    def _consume_open_extra_tablets(self, product_id, tablets_to_consume):
        """Consume extra tablets from active open packages by FIFO order."""
        remaining = max(0, int(tablets_to_consume or 0))
        if remaining <= 0:
            return

        rows = self.cursor.execute('''
            SELECT id, COALESCE(base_unit_qty, 0) AS base_unit_qty, COALESCE(extra_tablet_qty, 0) AS extra_tablet_qty
            FROM open_packages
            WHERE product_id = ? AND status = 'active' AND COALESCE(extra_tablet_qty, 0) > 0
            ORDER BY datetime(opened_date) ASC, id ASC
        ''', (product_id,)).fetchall()

        for row in rows:
            if remaining <= 0:
                break
            current_extra = int(row['extra_tablet_qty'] or 0)
            take = min(current_extra, remaining)
            new_extra = current_extra - take
            new_status = 'active'
            if int(row['base_unit_qty'] or 0) <= 0 and new_extra <= 0:
                new_status = 'used'

            self.cursor.execute(
                'UPDATE open_packages SET extra_tablet_qty = ?, status = ? WHERE id = ?',
                (new_extra, new_status, row['id'])
            )
            remaining -= take

        if remaining > 0:
            raise ValueError('เศษเม็ดที่เปิดแล้วไม่เพียงพอสำหรับการตัดจ่าย')

    def _consume_open_base_units(self, product_id, base_units_to_consume):
        """Consume base units from active open packages by FIFO order."""
        remaining = max(0, int(base_units_to_consume or 0))
        if remaining <= 0:
            return

        rows = self.cursor.execute('''
            SELECT id, COALESCE(base_unit_qty, 0) AS base_unit_qty, COALESCE(extra_tablet_qty, 0) AS extra_tablet_qty
            FROM open_packages
            WHERE product_id = ? AND status = 'active' AND COALESCE(base_unit_qty, 0) > 0
            ORDER BY datetime(opened_date) ASC, id ASC
        ''', (product_id,)).fetchall()

        for row in rows:
            if remaining <= 0:
                break

            current_base = int(row['base_unit_qty'] or 0)
            take = min(current_base, remaining)
            new_base = current_base - take
            new_status = 'active'
            if new_base <= 0 and int(row['extra_tablet_qty'] or 0) <= 0:
                new_status = 'used'

            self.cursor.execute(
                'UPDATE open_packages SET base_unit_qty = ?, status = ? WHERE id = ?',
                (new_base, new_status, row['id'])
            )
            remaining -= take

        if remaining > 0:
            raise ValueError('หน่วยย่อยที่เปิดแล้วไม่เพียงพอสำหรับการตัดจ่าย')
    
    # ============================================================================
    # 6. RECEIVE WITH UNIT CONVERSION
    # ============================================================================
    
    def receive_inventory(self, product_id, qty_package_unit, lot_number=None, emp_id='admin'):
        """
        Receive inventory in package_unit (e.g., 5 bottles)
        Automatically converts to base_unit for storage
        """
        info = self.get_product_unit_info(product_id)
        qty_base_unit = self.convert_package_to_base(product_id, qty_package_unit)
        
        try:
            # Add to stock
            self.cursor.execute('''
                UPDATE products SET stock = stock + ? WHERE id = ?
            ''', (qty_package_unit, product_id))
            
            # Create lot if needed
            if lot_number:
                self.cursor.execute('''
                    INSERT INTO product_lots (product_id, lot_number, qty, received_date)
                    VALUES (?, ?, ?, datetime('now'))
                ''', (product_id, lot_number, qty_base_unit))
            
            # Log transaction
            self.cursor.execute('''
                INSERT INTO transaction_logs 
                (emp_id, product_id, action, qty, qty_base_unit, qty_package_unit, timestamp)
                VALUES (?, ?, 'receive', ?, ?, ?, datetime('now'))
            ''', (emp_id, product_id, qty_package_unit, qty_base_unit, qty_package_unit))
            
            self.conn.commit()
            
            return {
                'success': True,
                'message': f"✅ รับเข้า {qty_package_unit} {info['package_unit']} ({qty_base_unit} {info['base_unit']}) สำเร็จ"
            }
            
        except Exception as e:
            self.conn.rollback()
            return {'success': False, 'message': f"❌ Error: {str(e)}"}


# ============================================================================
# HELPER FUNCTIONS FOR app.py
# ============================================================================

def get_withdrawal_info(db_connection, product_id, qty_base_unit):
    """
    Simple wrapper for get withdrawal info without database commit
    Used for preview before confirming
    """
    manager = UnitConversionManager(db_connection)
    info = manager.get_product_unit_info(product_id)
    calc = manager.calculate_withdrawal(product_id, qty_base_unit)
    
    return {
        'product_info': info,
        'withdrawal_calc': calc
    }


def process_withdrawal(db_connection, product_id, qty_base_unit, emp_id, lot_id=None):
    """
    Full withdrawal process with database update
    """
    manager = UnitConversionManager(db_connection)
    result = manager.apply_withdrawal(product_id, qty_base_unit, emp_id, lot_id)
    return result
