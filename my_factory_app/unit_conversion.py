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
            SELECT SUM(base_unit_qty) as total
            FROM open_packages
            WHERE product_id = ? AND status = 'active'
        ''', (product_id,))
        open_box_row = cursor.fetchone()
        open_box = {'total': open_box_row[0]} if open_box_row else {'total': 0}
        
        # Extract values safely
        base_unit = product.get('base_unit') or 'tablet'
        package_unit = product.get('package_unit') or product.get('unit') or 'box'
        conversion_rate = float(product.get('conversion_rate') or 1)
        stock_package_unit = product.get('stock') or 0
        open_box_qty = (open_box['total'] or 0) if open_box['total'] else 0
        
        # Convert to base units
        stock_base_unit = (stock_package_unit * conversion_rate) + open_box_qty
        
        return {
            'product_id': product_id,
            'name': product['name'],
            'code': product['code'],
            'base_unit': base_unit,
            'package_unit': package_unit,
            'conversion_rate': conversion_rate,
            'stock_base_unit': int(stock_base_unit),
            'stock_package_unit': stock_package_unit,
            'open_box_qty': int(open_box_qty),
            'has_open_box': open_box_qty > 0
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
        
        # Create transaction note
        note_parts = []
        package_unit = info['package_unit']
        if from_open_box > 0:
            note_parts.append(f"เบิกจาก{package_unit}ที่เปิดแล้ว {from_open_box} {info['base_unit']}")
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
            if calc['from_open_box'] > 0 and calc.get('open_box_id'):
                self.cursor.execute('''
                    UPDATE open_packages 
                    SET base_unit_qty = MAX(0, base_unit_qty - ?),
                        status = CASE WHEN base_unit_qty - ? <= 0 THEN 'used' ELSE 'active' END
                    WHERE id = ?
                ''', (calc['from_open_box'], calc['from_open_box'], calc['open_box_id']))
            
            # 3. Create new open_package if needed
            if calc['new_open_box_qty'] > 0:
                self.cursor.execute('''
                    INSERT INTO open_packages 
                    (product_id, lot_id, opened_date, base_unit_qty, package_unit_qty_before, status)
                    VALUES (?, ?, datetime('now'), ?, 1, 'active')
                ''', (product_id, lot_id, calc['new_open_box_qty']))
            
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
