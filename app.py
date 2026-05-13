from flask import Flask, jsonify, render_template, request, redirect, url_for, session
from werkzeug.security import generate_password_hash, check_password_hash
import mysql.connector
from mysql.connector import Error
import datetime
import requests
import os
from dotenv import load_dotenv
from datetime import date, datetime
import time
import win32print
from flask import jsonify
from functools import wraps
import pandas as pd
from io import BytesIO
from flask import send_file



load_dotenv()
PAYMONGO_SECRET_KEY = os.getenv('PAYMONGO_SECRET_KEY')
db_user = os.getenv("DB_USER")
db_password = os.getenv("DB_PASSWORD")

app = Flask(__name__)
app.secret_key = 'js_motoworks_super_secret_key'

from functools import wraps

# SECURITY CHECK
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login')) 
        return f(*args, **kwargs)
    return decorated_function

def admin_only(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session or session.get('role') != 'Admin':
            return redirect(url_for('pos_page'))
        return f(*args, **kwargs)
    return decorated_function

# DATABASE CONFIG
db_config = {
    'host': 'localhost',
    'user': 'root',
    'password': '',
    'database': 'js_motoworks_db'
}

def get_db_connection():
    try:
        connection = mysql.connector.connect(**db_config)
        return connection
    except Error as e:
        print(f"Database Error: {e}")
        return None

# HOME
@app.route('/')
def home():
    return render_template('booking.html')

def generate_smart_sku(category, brand, cursor):
    cat_prefix = category[:3].upper() if len(category) >= 3 else category.upper()
    brand_prefix = brand[:3].upper() if len(brand) >= 3 else brand.upper()
    
    cursor.execute("SELECT COUNT(*) AS total_items FROM inventory WHERE category = %s AND brand = %s", (category, brand))
    result = cursor.fetchone()
    count = result['total_items'] if result else 0

    new_number = str(count + 1).zfill(3) 
    return f"{cat_prefix}-{brand_prefix}-{new_number}"


# DASHBOARD ROUTE
@app.route('/dashboard')
@admin_only
def dashboard():
    # 1. Check kung naka-login
    if 'user_id' not in session:
        return redirect('/login')

    if session.get('role') != 'Admin':
        return redirect(url_for('pos_page'))

    conn = get_db_connection()
    if not conn:
        return "Database Connection Error"

    cursor = conn.cursor(dictionary=True)

    # 1. Total Sales Today
    today = date.today()
    cursor.execute("SELECT SUM(total_amount) as total FROM sales_transactions WHERE DATE(transaction_date) = %s", (today,))
    result_sales = cursor.fetchone()
    sales_today = result_sales['total'] if result_sales and result_sales['total'] else 0

    # 2. Low Stock Count
    cursor.execute("SELECT COUNT(*) as low_stock_count FROM inventory WHERE stock_qty <= 5")
    result_low = cursor.fetchone()
    low_stock = result_low['low_stock_count'] if result_low else 0

    # 3. Recent Transactions
    cursor.execute("SELECT receipt_no, total_amount, payment_method, transaction_date FROM sales_transactions ORDER BY transaction_date DESC LIMIT 5")
    recent_sales = cursor.fetchall()

    # 4. CHART DATA: Sales for the Last 7 Days
    cursor.execute("""
        SELECT DATE(transaction_date) as date, SUM(total_amount) as daily_total
        FROM sales_transactions
        WHERE transaction_date >= DATE_SUB(CURDATE(), INTERVAL 7 DAY)
        GROUP BY DATE(transaction_date)
        ORDER BY DATE(transaction_date) ASC
    """)
    weekly_sales = cursor.fetchall()
    
    chart_dates = [str(row['date']) for row in weekly_sales]
    chart_sales = [float(row['daily_total']) for row in weekly_sales]

    # 5. CHART DATA: Payment Method Distribution
    cursor.execute("""
        SELECT payment_method, SUM(total_amount) as total 
        FROM sales_transactions 
        GROUP BY payment_method
    """)
    pay_methods = cursor.fetchall()
    
    pay_labels = [row['payment_method'] for row in pay_methods]
    pay_data = [float(row['total']) for row in pay_methods]

    conn.close()

    return render_template('dashboard.html', 
                           sales_today=sales_today, 
                           low_stock=low_stock, 
                           recent_sales=recent_sales,
                           chart_dates=chart_dates,
                           chart_sales=chart_sales,
                           pay_labels=pay_labels,
                           pay_data=pay_data)

# INVENTORY
# INVENTORY
@app.route('/inventory', methods=['GET', 'POST'])
def inventory_page(): 
    if 'user_id' not in session:
        return redirect(url_for('login'))
    if session.get('role') != 'Admin':
        return redirect(url_for('pos_page'))
    
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    if request.method == 'POST':
        scanned_sku = request.form['sku'].strip()
        item_name = request.form['item_name']
        brand = request.form['brand']
        category = request.form['category']
        price = request.form['price']
        added_qty = int(request.form['stock_qty'])
        
        cursor.execute("SELECT * FROM inventory WHERE sku = %s", (scanned_sku,))
        existing_item = cursor.fetchone()
        
        if existing_item:
            cursor.execute("""
                UPDATE inventory 
                SET stock_qty = stock_qty + %s 
                WHERE sku = %s
            """, (added_qty, scanned_sku))
            
            current_user = session.get('username', 'Admin')
            cursor.execute(
                "INSERT INTO stock_logs (sku, action, qty, username, remarks) VALUES (%s, %s, %s, %s, %s)",
                (scanned_sku, 'Stock In', added_qty, current_user, "Added via New Item Form")
            )
        else:
            sql = "INSERT INTO inventory (sku, item_name, brand, category, price, stock_qty) VALUES (%s, %s, %s, %s, %s, %s)"
            val = (scanned_sku, item_name, brand, category, price, added_qty)
            cursor.execute(sql, val)
            
        conn.commit()
        return redirect(url_for('inventory_page')) 
        
    cursor.execute("SELECT * FROM inventory ORDER BY item_id DESC")
    items = cursor.fetchall()
    conn.close()
    
    return render_template('inventory.html', items=items)

# 2. POS
@app.route('/pos')
def pos_page():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM services")
    services = cursor.fetchall()
    conn.close()
    return render_template('pos.html', services=services)


# CHECKOUT LOgiC
@app.route('/checkout', methods=['POST'])
def checkout():
    if 'user_id' not in session:
        return jsonify({"status": "error", "message": "Unauthorized"}), 401

    data = request.json
    cart = data.get('cart')
    payment_method = data.get('payment_method')
    total_amount = data.get('total_amount')
    gcash_ref = data.get('gcash_ref')

    # 1. Generate Receipt Number
    receipt_no = f"JS-{date.today().strftime('%Y%m%d')}-{int(time.time()) % 1000:03d}"

    conn = get_db_connection()
    if not conn:
        return jsonify({"status": "error", "message": "Database Connection Failed"}), 500
    #database transaction
    try:
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO sales_transactions (receipt_no, total_amount, payment_method, gcash_reference, user_id)
            VALUES (%s, %s, %s, %s, %s)
        """, (receipt_no, total_amount, payment_method, gcash_ref, session['user_id']))
        
        transaction_id = cursor.lastrowid

        for item in cart:
            cursor.execute("""
                INSERT INTO sales_items (transaction_id, item_description, item_type, qty, unit_price, subtotal)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (transaction_id, item['name'], item['type'], item['qty'], item['price'], (item['qty'] * item['price'])))

            # AUTO-DEDUCTION
            if item['type'] == 'Part':
                cursor.execute("""
                    UPDATE inventory 
                    SET stock_qty = stock_qty - %s 
                    WHERE sku = %s
                """, (item['qty'], item['sku']))

        conn.commit()
    except Exception as e:
        conn.rollback()
        if conn.is_connected():
            conn.close()
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if conn.is_connected():
            conn.close()

    try:
        cashier = session.get('username', f"User {session['user_id']}")
        
        print_receipt_direct(
            receipt_no=receipt_no,
            cashier_name=cashier,
            cart=cart,
            total=total_amount,
            method=payment_method,
            gcash_ref=gcash_ref
        )
    except Exception as print_err:
        print(f"⚠️ Transaction Saved but Printer Error: {print_err}")

    # Successful Transaction Response
    return jsonify({"status": "success", "message": "Transaction completed!", "receipt": receipt_no})

# ONLINE BOOKING ROUTE
@app.route('/book', methods=['GET', 'POST'])
def book_appointment():
    if request.method == 'POST':
        name = request.form['customer_name']
        contact = request.form['contact_number']
        motor = request.form['motorcycle_model']
        
        category = request.form['service_category']
        notes = request.form.get('customer_notes', '')
        
        service = f"{category} (Issue: {notes})" if notes else category
        
        date = request.form['appointment_date']
        time = request.form['appointment_time']

        conn = get_db_connection()
        if conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO appointments (customer_name, contact_number, motorcycle_model, service_requested, appointment_date, appointment_time) VALUES (%s, %s, %s, %s, %s, %s)",
                (name, contact, motor, service, date, time)
            )
            conn.commit()
            conn.close()
            
            return render_template('book_success.html', name=name, date=date, time=time)

    return render_template('booking.html')

# ADMIN APPOINTMENTS DASHBOARD
@app.route('/appointments')
def appointments_page():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    if session.get('role') != 'Admin':
        return redirect(url_for('pos_page'))

    conn = get_db_connection()
    if conn:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM appointments ORDER BY appointment_date ASC, appointment_time ASC")
        appointments = cursor.fetchall()
        conn.close()
        return render_template('appointments.html', appointments=appointments)
    return "Database connection error."

# UPDATE APPOINTMENT STATUS 
@app.route('/update_appointment/<int:id>', methods=['POST'])
def update_appointment(id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    if session.get('role') != 'Admin':
        return redirect(url_for('pos_page'))

    new_status = request.form['status']
    conn = get_db_connection()
    if conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE appointments SET status = %s WHERE appointment_id = %s", (new_status, id))
        conn.commit()
        conn.close()
    return redirect(url_for('appointments_page'))

@app.route('/delete_appointment/<int:id>', methods=['POST'])
def delete_appointment(id):
    if 'user_id' not in session or session.get('role') != 'Admin':
        return redirect(url_for('pos_page'))

    conn = get_db_connection()
    if conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM appointments WHERE appointment_id = %s", (id,))
        conn.commit()
        conn.close()
    return redirect(url_for('appointments_page'))

# LOGIN 
@app.route('/login', methods=['GET', 'POST'])
def login():
    conn = get_db_connection()
    if conn:
        cursor = conn.cursor(dictionary=True)
        
        cursor.execute("SELECT COUNT(*) as user_count FROM users")
        result = cursor.fetchone()
        
        if result['user_count'] == 0:
            default_hash = generate_password_hash('admin123')
            cursor.execute("INSERT INTO users (username, password_hash, full_name, role) VALUES (%s, %s, %s, %s)", 
                           ('admin', default_hash, 'Main Admin', 'Admin'))
            conn.commit()

        if request.method == 'POST':
            username = request.form['username']
            password = request.form['password']
            
            cursor.execute("SELECT * FROM users WHERE username = %s", (username,))
            user = cursor.fetchone()
            
            if user and check_password_hash(user['password_hash'], password):
                session['user_id'] = user['user_id']
                session['username'] = user['username']
                session['role'] = user['role']
                session['full_name'] = user['full_name']
                
                cursor.fetchall() 
                conn.close()

                if session['role'] == 'Admin':
                    return redirect(url_for('dashboard'))
                else:
                    return redirect(url_for('pos_page'))
            else:
                cursor.fetchall()
                conn.close()
                return render_template('login.html', error="Invalid Username or Password!")
                
        conn.close()
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# SALES HISTORY & REPORTS
@app.route('/sales')
def sales_history():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')

    conn = get_db_connection()
    if conn:
        cursor = conn.cursor(dictionary=True)
        
        if start_date and end_date:
            query = "SELECT * FROM sales_transactions WHERE transaction_date BETWEEN %s AND %s ORDER BY transaction_date DESC"
            cursor.execute(query, (start_date + " 00:00:00", end_date + " 23:59:59"))
        else:
            cursor.execute("SELECT * FROM sales_transactions ORDER BY transaction_date DESC LIMIT 200")
            
        sales = cursor.fetchall()
        
        total_sales = sum(sale['total_amount'] for sale in sales)
        
        conn.close()
        
        return render_template('sales_history.html', 
                               sales=sales, 
                               start_date=start_date, 
                               end_date=end_date,
                               total_sales=total_sales)
    return "Database connection error."

# USER MANAGEMENT
@app.route('/users', methods=['GET', 'POST'])
def manage_users():
    if 'user_id' not in session or session.get('role') != 'Admin':
        return redirect(url_for('pos_page'))
        
    conn = get_db_connection()
    if conn:
        cursor = conn.cursor(dictionary=True)
        
        if request.method == 'POST':
            new_username = request.form['username']
            new_password = request.form['password']
            full_name = request.form['full_name']
            role = request.form['role']
            
            # hash password new user
            hashed_pw = generate_password_hash(new_password)
            
            try:
                cursor.execute("INSERT INTO users (username, password_hash, full_name, role) VALUES (%s, %s, %s, %s)",
                               (new_username, hashed_pw, full_name, role))
                conn.commit()
            except Error as e:
                return f"Error adding user: {e}"
            return redirect(url_for('manage_users'))
            
        cursor.execute("SELECT user_id, username, full_name, role, created_at FROM users ORDER BY created_at DESC")
        users = cursor.fetchall()
        conn.close()
        
        return render_template('users.html', users=users)
    return "Database connection error."

#DELETE USER ROUTE
@app.route('/delete_user/<int:id>', methods=['POST'])
def delete_user(id):
    if 'user_id' not in session or session.get('role') != 'Admin':
        return redirect(url_for('pos_page'))

    if id == session.get('user_id'):
        return "Error: You cannot delete your own active account.", 403

    conn = get_db_connection()
    if conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM users WHERE user_id = %s", (id,))
        conn.commit()
        conn.close()
    return redirect(url_for('manage_users'))

# RESTOCK ITEM (Stock In)
@app.route('/restock', methods=['POST'])
def restock_item():
    if 'user_id' not in session or session.get('role') != 'Admin':
        return redirect(url_for('inventory_page'))
        
    sku = request.form['sku']
    added_qty = int(request.form['added_qty'])
    current_user = session.get('username')
    
    conn = get_db_connection()
    if conn:
        cursor = conn.cursor()
        # 1. Update stock inventory
        cursor.execute("UPDATE inventory SET stock_qty = stock_qty + %s WHERE sku = %s", (added_qty, sku))
        # 2.record logs
        cursor.execute(
            "INSERT INTO stock_logs (sku, action, qty, username, remarks) VALUES (%s, %s, %s, %s, %s)",
            (sku, 'Stock In', added_qty, current_user, "Manual Restock by Admin")
        )
        conn.commit()
        conn.close()
    return redirect(url_for('inventory_page'))

# INVENTORY AUDIT LOGS 
@app.route('/audit_logs')
def audit_logs():
    if 'user_id' not in session or session.get('role') != 'Admin':
        return redirect(url_for('pos_page'))
        
    conn = get_db_connection()
    if conn:
        cursor = conn.cursor(dictionary=True)
        query = """
            SELECT s.*, i.item_name 
            FROM stock_logs s 
            LEFT JOIN inventory i ON s.sku = i.sku 
            ORDER BY s.log_date DESC
        """
        cursor.execute(query)
        logs = cursor.fetchall()
        conn.close()
        return render_template('audit_logs.html', logs=logs)
    return "Database connection error."

# THERMAL RECEIPT PRINTING
@app.route('/print_receipt/<receipt_no>')
def print_receipt(receipt_no):
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    conn = get_db_connection()
    if conn:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM sales_transactions WHERE receipt_no = %s", (receipt_no,))
        transaction = cursor.fetchone()
        
        if transaction:
            cursor.execute("SELECT * FROM sales_items WHERE transaction_id = %s", (transaction['transaction_id'],))
            items = cursor.fetchall()
            conn.close()
            
            return render_template('receipt.html', transaction=transaction, items=items)
        
        conn.close()
    return "Receipt not found."


@app.route('/reprint_direct/<receipt_no>', methods=['POST'])
def reprint_direct(receipt_no):
    if 'user_id' not in session:
        return jsonify({"status": "error", "message": "Unauthorized"}), 401

    conn = get_db_connection()
    if not conn:
        return jsonify({"status": "error", "message": "Database Error"}), 500

    try:
        cursor = conn.cursor(dictionary=True)
        
        # 1. transaction details
        cursor.execute("""
            SELECT t.*, u.username as cashier_name 
            FROM sales_transactions t
            LEFT JOIN users u ON t.user_id = u.user_id
            WHERE t.receipt_no = %s
        """, (receipt_no,))
        transaction = cursor.fetchone()

        if not transaction:
            return jsonify({"status": "error", "message": "Receipt not found"}), 404

        # 2. items
        cursor.execute("SELECT * FROM sales_items WHERE transaction_id = %s", (transaction['transaction_id'],))
        items = cursor.fetchall()

        # 3.format of items on print function
        cart_format = []
        for item in items:
            cart_format.append({
                'name': item['item_description'],
                'qty': item['qty'],
                'price': item['unit_price']
            })

        # Call Printer Function
        cashier = transaction['cashier_name'] if transaction['cashier_name'] else "Admin"
        
        print_receipt_direct(
            receipt_no=transaction['receipt_no'],
            cashier_name=cashier,
            cart=cart_format,
            total=transaction['total_amount'],
            method=transaction['payment_method'],
            gcash_ref=transaction['gcash_reference']
        )

        return jsonify({"status": "success", "message": "Printing directly to POS-58!"})

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if conn.is_connected():
            conn.close()

#PAYMONGO GCASH INTEGRATION
@app.route('/api/generate_paymongo_link', methods=['POST'])
def generate_paymongo_link():
    if 'user_id' not in session:
        return jsonify({"status": "error", "message": "Unauthorized"})

    data = request.json
    total_amount = data.get('total_amount')

    amount_in_cents = int(float(total_amount) * 100)

    url = "https://api.paymongo.com/v1/links"
    
    payload = {
        "data": {
            "attributes": {
                "amount": amount_in_cents,
                "description": f"JS Motoworks POS - {session['username']}",
                "remarks": "POS Transaction"
            }
        }
    }
    
    try:
        response = requests.post(
            url, 
            json=payload, 
            auth=(PAYMONGO_SECRET_KEY, '')
        )
        response_data = response.json()
        
        if response.status_code == 200:
            checkout_url = response_data['data']['attributes']['checkout_url']
            reference_number = response_data['data']['attributes']['reference_number']
            return jsonify({
                "status": "success", 
                "checkout_url": checkout_url, 
                "reference_number": reference_number
            })
        else:
            return jsonify({"status": "error", "message": "PayMongo API Error"})
            
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

# PRINT RECEIPT
def print_receipt_direct(receipt_no, cashier_name, cart, total, method, gcash_ref):
    printer_name = "POS-58" #  IN CONTROL PANEL
    
    date_str = datetime.datetime.now().strftime('%y-%m-%d %H:%M')
    
    receipt_text = f"""
JS MOTOWORKS
#13 Sandoval Street, Barangay Pinagbuhatan, Pasig City, Pasig, Philippines, 1600
CP: 0998 991 3579
--------------------------------
OR#: {receipt_no}
Date: {date_str}
Cashier: {cashier_name}
--------------------------------
QTY  ITEM                  AMT
"""
    for item in cart:
        desc = str(item['name'])[:16].ljust(16)
        qty = str(item['qty']).ljust(4)
        subtotal = float(item['qty']) * float(item['price'])
        amt = f"{subtotal:.2f}".rjust(8)
        receipt_text += f"{qty} {desc} {amt}\n"

    receipt_text += f"""
--------------------------------
TOTAL:             PHP {float(total):.2f}
Method: {method}
"""
    if method == 'GCash' and gcash_ref:
        receipt_text += f"Ref: {gcash_ref}\n"

    receipt_text += """
--------------------------------
THANK YOU! RIDE SAFE!




"""
    
    try:
        hprinter = win32print.OpenPrinter(printer_name)
        try:
            hjob = win32print.StartDocPrinter(hprinter, 1, ("Receipt", None, "RAW"))
            win32print.StartPagePrinter(hprinter)
            win32print.WritePrinter(hprinter, receipt_text.encode('utf-8'))
            win32print.EndPagePrinter(hprinter)
        finally:
            win32print.EndDocPrinter(hprinter)
            win32print.ClosePrinter(hprinter)
        print("✅ Receipt printed directly!")
    except Exception as e:
        print(f"❌ Printer Error: {e}")

# GET ITEM FOR BARCODE SCANNER
@app.route('/api/get_item/<sku>', methods=['GET'])
def get_item(sku):
    clean_sku = sku.strip()

    conn = get_db_connection()
    if not conn:
        return jsonify({'status': 'error', 'message': 'Database connection error'})

    cursor = conn.cursor(dictionary=True)
    
    cursor.execute("SELECT * FROM inventory WHERE sku = %s", (clean_sku,))
    item = cursor.fetchone()
    conn.close()

    if item:
        if item['stock_qty'] <= 0:
            return jsonify({'status': 'error', 'message': f"Item '{item['item_name']}' is out of stock!"})
            
        return jsonify({
            'status': 'success',
            'data': {
                'sku': item['sku'],
                'item_name': item['item_name'],
                'price': item['price'],
                'stock_qty': item['stock_qty']
            }
        })
    else:
        return jsonify({'status': 'error', 'message': f'Barcode {clean_sku} not found in inventory.'})

# UPDATE BARCODE
@app.route('/update_barcode', methods=['POST'])
def update_barcode():
    if 'user_id' not in session or session.get('role') != 'Admin':
        return redirect(url_for('pos_page'))

    old_sku = request.form.get('old_sku')
    new_barcode = request.form.get('new_barcode').strip() 

    conn = get_db_connection()
    if conn:
        cursor = conn.cursor(dictionary=True)
        
        # 1. Check  barcode to other item
        cursor.execute("SELECT * FROM inventory WHERE sku = %s", (new_barcode,))
        existing_item = cursor.fetchone()
        
        if existing_item:
            conn.close()
            return f"Error: Barcode '{new_barcode}' is already assigned to {existing_item['item_name']}."

        # 2. update SKU
        cursor.execute("UPDATE inventory SET sku = %s WHERE sku = %s", (new_barcode, old_sku))
        conn.commit()
        conn.close()
        
    return redirect('/inventory')

# EDIT ITEM ROUTE
@app.route('/edit_item', methods=['POST'])
def edit_item():
    if 'user_id' not in session or session.get('role') != 'Admin':
        return redirect(url_for('pos_page'))

    sku = request.form.get('sku')
    item_name = request.form.get('item_name')
    brand = request.form.get('brand')
    category = request.form.get('category')
    price = request.form.get('price')

    conn = get_db_connection()
    if conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE inventory 
            SET item_name = %s, brand = %s, category = %s, price = %s 
            WHERE sku = %s
        """, (item_name, brand, category, price, sku))
        conn.commit()
        conn.close()
        
    return redirect('/inventory')

# Excel file
@app.route('/export_sales_excel')
@admin_only
def export_sales_excel():
    conn = get_db_connection()
    
    df_transactions = pd.read_sql("SELECT * FROM sales_transactions ORDER BY 1 DESC", conn)
    
    df_items = pd.read_sql("SELECT * FROM sales_items", conn)
    
    conn.close()

    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df_transactions.to_excel(writer, index=False, sheet_name='Transactions History')
        df_items.to_excel(writer, index=False, sheet_name='Items Sold')
    
    output.seek(0)
    
    date_today = datetime.now().strftime('%Y-%m-%d')
    return send_file(
        output, 
        download_name=f"JS_Motoworks_Sales_{date_today}.xlsx", 
        as_attachment=True
    )

# Pinalitan natin ng <string:item_sku> at 'item_sku' ang variable
@app.route('/delete_item/<string:item_sku>', methods=['POST'])
def delete_item(item_sku):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("DELETE FROM inventory WHERE sku = %s", (item_sku,))
        conn.commit()

        if cursor.rowcount > 0:
            return jsonify({"success": True, "message": "Item deleted successfully!"})
        else:
            return jsonify({"success": False, "message": "Hindi nahanap ang item."})

    except mysql.connector.errors.IntegrityError:
        return jsonify({"success": False, "message": "Cannot be deleted: This item already has a sales record. Just set the stock to zero for audit purposes."})
    
    except Exception as e:
        print(f"ERROR SA DELETE: {e}")
        return jsonify({"success": False, "message": f"Database Error: {str(e)}"})
    
#DELETE SALES TRANSACT
@app.route('/purge_sales', methods=['POST'])
@admin_only
def purge_sales():
    target_month = request.form.get('target_month')
    admin_password = request.form.get('admin_password')
    
    if not target_month or not admin_password:
         return jsonify({"success": False, "message": "Month and Admin Password are required."})

    conn = get_db_connection()
    if not conn:
        return jsonify({"success": False, "message": "Database error."})

    try:
        cursor = conn.cursor(dictionary=True)

        #VERIFY ADMIN PASSWORD 
        cursor.execute("SELECT password_hash FROM users WHERE user_id = %s", (session['user_id'],))
        admin = cursor.fetchone()
        
        if not admin or not check_password_hash(admin['password_hash'], admin_password):
            return jsonify({"success": False, "message": "Incorrect Admin Password"})
        cursor.execute("""
            SELECT transaction_id FROM sales_transactions 
            WHERE DATE_FORMAT(transaction_date, '%Y-%m') = %s
        """, (target_month,))
        transactions = cursor.fetchall()

        if not transactions:
            return jsonify({"success": False, "message": f"Walang records na nahanap para sa buwan ng {target_month}."})

        transaction_ids = [t['transaction_id'] for t in transactions]
        format_strings = ','.join(['%s'] * len(transaction_ids))
        cursor.execute(f"DELETE FROM sales_items WHERE transaction_id IN ({format_strings})", tuple(transaction_ids))
        cursor.execute(f"DELETE FROM sales_transactions WHERE transaction_id IN ({format_strings})", tuple(transaction_ids))
        current_admin = session.get('username')
        cursor.execute("""
            INSERT INTO stock_logs (sku, action, qty, username, remarks) 
            VALUES ('SYSTEM', 'DATA PURGE', 0, %s, %s)
        """, (current_admin, f"Purged {len(transaction_ids)} sales records for {target_month}"))

        conn.commit()
        return jsonify({"success": True, "message": f"Success! {len(transaction_ids)} sales records for {target_month} have been purged."})

    except Exception as e:
        conn.rollback()
        print(f"ERROR SA PURGE: {e}")
        return jsonify({"success": False, "message": f"Database Error: {str(e)}"})
    finally:
        conn.close()

# RUN
if __name__ == '__main__':
    app.run(debug=True)