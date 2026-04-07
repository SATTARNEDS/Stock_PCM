"""
HOW TO INTEGRATE UNIT CONVERSION SYSTEM
Location: Add this to your app.py after the imports
"""

# ============================================================
# STEP 1: Add this import at the top of app.py
# ============================================================
from unit_conversion import UnitConversionManager, get_withdrawal_info, process_withdrawal

# ============================================================
# STEP 2: Modify the /add_to_cart route
# ============================================================

# OLD CODE (ต้องแก้):
"""
@app.route('/add_to_cart', methods=['POST'])
def add_to_cart():
    emp_id = request.form.get('emp_id')
    product_id = request.form.get('product_id')
    qty = int(request.form.get('qty', 1))
    current_search = request.form.get('current_search', '')
    current_cat = request.form.get('current_cat', '')

    conn = get_db_connection()
    product = conn.execute('SELECT * FROM products WHERE id = ?', (product_id,)).fetchone()

    if product and product['stock'] >= qty:
        # ... add to cart logic
"""

# NEW CODE (ปรับใหม่ให้รองรับเม็ด):
@app.route('/add_to_cart', methods=['POST'])
def add_to_cart():
    emp_id = request.form.get('emp_id')
    product_id = request.form.get('product_id')
    qty = int(request.form.get('qty', 1))
    qty_unit = request.form.get('qty_unit', 'base')  # 'base' = เม็ด, 'package' = ขวด
    current_search = request.form.get('current_search', '')
    current_cat = request.form.get('current_cat', '')

    conn = get_db_connection()
    
    # ใช้ Unit Conversion System
    manager = UnitConversionManager(conn)
    
    try:
        product_info = manager.get_product_unit_info(product_id)
        
        # Convert to base_unit if user input is in package_unit
        if qty_unit == 'package':
            qty_base_unit = manager.convert_package_to_base(product_id, qty)
        else:
            qty_base_unit = qty
        
        # Check availability
        check = manager.check_stock_available(product_id, qty_base_unit)
        
        if check['available']:
            # Calculate withdrawal details
            withdrawal_calc = manager.calculate_withdrawal(product_id, qty_base_unit)
            
            if withdrawal_calc['can_fulfill']:
                # Add to cart
                existing_item = conn.execute(
                    'SELECT * FROM carts WHERE emp_id = ? AND product_id = ?',
                    (emp_id, product_id)
                ).fetchone()
                
                if existing_item:
                    conn.execute(
                        'UPDATE carts SET qty = qty + ? WHERE id = ?',
                        (qty_base_unit, existing_item['id'])
                    )
                else:
                    conn.execute(
                        'INSERT INTO carts (emp_id, product_id, qty) VALUES (?, ?, ?)',
                        (emp_id, product_id, qty_base_unit)
                    )
                
                # ⚠️ Don't reduce stock yet! (reduce only on confirmation)
                conn.commit()
                
                flash(
                    f'🛒 เพิ่ม {product_info["name"]}: {qty_base_unit} {product_info["base_unit"]} = {withdrawal_calc["total_packages_used"]:.1f} {product_info["package_unit"]} ✅',
                    'success'
                )
            else:
                flash(f'❌ {withdrawal_calc["message"]}', 'danger')
        else:
            flash(f'❌ {check["message"]}', 'danger')
    
    except Exception as e:
        flash(f'❌ Error: {str(e)}', 'danger')
    finally:
        conn.close()
    
    return redirect(url_for('menu', emp_id=emp_id, search=current_search, category=current_cat))


# ============================================================
# STEP 3: Create NEW route for withdrawal confirmation
# ============================================================

@app.route('/confirm_withdrawal_with_units', methods=['POST'])
def confirm_withdrawal_with_units():
    """
    Enhanced withdrawal confirmation that handles unit conversion
    """
    emp_id = request.form.get('emp_id')
    conn = get_db_connection()
    
    try:
        user = conn.execute(
            'SELECT * FROM users WHERE emp_id = ?', (emp_id,)
        ).fetchone()
        
        if not user:
            return redirect(url_for('index'))
        
        # Get all cart items
        cart_items = conn.execute('''
            SELECT c.*, p.name, p.stock, p.unit, p.id as product_id
            FROM carts c 
            JOIN products p ON c.product_id = p.id 
            WHERE c.emp_id = ?
        ''', (emp_id,)).fetchall()
        
        if not cart_items:
            flash('❌ ตะกร้าว่าง', 'danger')
            return redirect(url_for('menu', emp_id=emp_id))
        
        manager = UnitConversionManager(conn)
        total_items = 0
        failed_items = []
        
        # Process each cart item
        for cart_item in cart_items:
            product_id = cart_item['product_id']
            qty_base_unit = cart_item['qty']  # qty is in base units
            
            # Process withdrawal
            result = manager.apply_withdrawal(
                product_id=product_id,
                qty_base_unit=qty_base_unit,
                emp_id=emp_id,
                lot_id=None,
                use_open_box=True
            )
            
            if result['success']:
                total_items += 1
            else:
                failed_items.append({
                    'name': cart_item['name'],
                    'error': result['message']
                })
        
        # Clear cart
        conn.execute('DELETE FROM carts WHERE emp_id = ?', (emp_id,))
        conn.commit()
        
        if failed_items:
            errors_html = '<br>'.join([f"❌ {item['name']}: {item['error']}" for item in failed_items])
            flash(f'⚠️ เบิก {total_items} รายการสำเร็จ<br>{errors_html}', 'warning')
        else:
            flash(f'✅ เบิกยา {total_items} รายการสำเร็จ!', 'success')
        
        return redirect(url_for('menu', emp_id=emp_id))
    
    except Exception as e:
        conn.rollback()
        flash(f'❌ Error: {str(e)}', 'danger')
    finally:
        conn.close()
    
    return redirect(url_for('menu', emp_id=emp_id))


# ============================================================
# STEP 4: Create API endpoint for unit conversion preview
# ============================================================

@app.route('/api/preview_withdrawal', methods=['POST'])
def preview_withdrawal():
    """
    AJAX endpoint to preview withdrawal before confirming
    Returns: JSON with unit conversion calculations
    """
    data = request.get_json()
    product_id = data.get('product_id')
    qty_base_unit = data.get('qty_base_unit', 1)
    
    try:
        conn = get_db_connection()
        info = get_withdrawal_info(conn, product_id, qty_base_unit)
        conn.close()
        
        return jsonify({
            'success': True,
            'product': info['product_info'],
            'calculation': info['withdrawal_calc']
        })
    
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 400


# ============================================================
# STEP 5: Update menu.html template to show unit info
# ============================================================

"""
In templates/menu.html or templates/product_list_partial.html, 
update the product display:

BEFORE:
  <div>{{ product.name }} ({{ product.stock }} {{ product.unit }})</div>
  <input type="number" name="qty" value="1">

AFTER:
  <div>{{ product.name }}</div>
  <small>📦 Stock: {{ product.stock }} {{ product.unit }}</small>
  <small>💊 = {{ product.stock * product.conversion_rate }} tablets</small>
  
  <select name="qty_unit">
    <option value="base">เม็ด ({{ product.base_unit }})</option>
    <option value="package">{{ product.unit }} ({{ product.package_unit }})</option>
  </select>
  <input type="number" name="qty" value="1" id="qty_input">
  
  <div id="preview" style="font-size: 0.9em; color: #666;">
    <!-- AJAX will update this -->
  </div>
```

ADD JavaScript:
```javascript
// Show preview when qty changes
document.getElementById('qty_input').addEventListener('change', function() {
  const product_id = this.closest('form').product_id.value;
  const qty_base_unit = parseInt(this.value) || 1;
  
  fetch('/api/preview_withdrawal', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ product_id, qty_base_unit })
  })
  .then(r => r.json())
  .then(data => {
    if (data.success && data.calculation.can_fulfill) {
      document.getElementById('preview').innerHTML = 
        `✅ ${data.calculation.message}`;
    } else if (!data.calculation.can_fulfill) {
      document.getElementById('preview').innerHTML = 
        `❌ ${data.calculation.message}`;
    }
  });
});
```
"""

# ============================================================
# STEP 6: Database Migration Script
# ============================================================

"""
Run this SQL to update existing database:

1. Add new columns to products:
   ALTER TABLE products ADD COLUMN base_unit TEXT DEFAULT 'tablet';
   ALTER TABLE products ADD COLUMN package_unit TEXT;
   ALTER TABLE products ADD COLUMN conversion_rate REAL DEFAULT 1;

2. Create open_packages table:
   CREATE TABLE IF NOT EXISTS open_packages (
       id INTEGER PRIMARY KEY AUTOINCREMENT,
       product_id INTEGER NOT NULL,
       lot_id INTEGER,
       opened_date TEXT,
       base_unit_qty INTEGER DEFAULT 0,
       package_unit_qty_before REAL DEFAULT 1,
       status TEXT DEFAULT 'active',
       FOREIGN KEY (product_id) REFERENCES products(id)
   );

3. Create unit_conversions table:
   CREATE TABLE IF NOT EXISTS unit_conversions (
       id INTEGER PRIMARY KEY AUTOINCREMENT,
       product_id INTEGER UNIQUE,
       base_unit TEXT,
       package_unit TEXT,
       conversion_rate REAL,
       created_at TEXT,
       FOREIGN KEY (product_id) REFERENCES products(id)
   );

4. Update transaction_logs:
   ALTER TABLE transaction_logs ADD COLUMN qty_base_unit INTEGER;
   ALTER TABLE transaction_logs ADD COLUMN qty_package_unit REAL;
   ALTER TABLE transaction_logs ADD COLUMN note TEXT;

5. Insert conversion rates for existing products:
   INSERT INTO unit_conversions (product_id, base_unit, package_unit, conversion_rate)
   SELECT id, 'tablet', unit, 20 FROM products WHERE unit = 'bottle'
   
   (Adjust conversion_rate based on your actual products)
"""

# ============================================================
# EXAMPLE USAGE IN HTML FORM
# ============================================================

"""
<form method="POST" action="/add_to_cart">
    <input type="hidden" name="emp_id" value="{{ user.emp_id }}">
    <input type="hidden" name="product_id" value="{{ product.id }}">
    
    <label>จำนวน</label>
    <input type="number" name="qty" value="1" min="1">
    
    <label>หน่วย</label>
    <select name="qty_unit">
        <option value="base">{{product.base_unit or 'tablet'}}</option>
        <option value="package">{{product.package_unit or product.unit}}</option>
    </select>
    
    <button type="submit" class="btn btn-primary">🛒 เพิ่มลงตะกร้า</button>
</form>

<!-- Preview will show: "✅ เบิก 23 เม็ด = 1.15 ขวด" -->
"""
