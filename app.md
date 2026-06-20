from flask import Flask, jsonify, render_template, request, redirect, url_for, session
from werkzeug.security import generate_password_hash, check_password_hash
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
from supabase import create_client, Client

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
PAYMONGO_SECRET_KEY = os.getenv('PAYMONGO_SECRET_KEY')

app = Flask(**name**)
app.secret_key = 'js_motoworks_super_secret_key'

from functools import wraps

#Login Function
def login_user(username, password_input): # Kukunin natin ang user data base sa username
response = supabase.table("system_users").select("\*").eq("username", username).execute()

    if len(response.data) > 0:
        user = response.data[0]

        if user["password_hash"] == password_input and user["is_active"]:
            print(f"Login successful! Role: {user['role']}")
            return {"status": "success", "user_id": user["id"], "role": user["role"]}
        else:
            return {"status": "error", "message": "Invalid password or inactive account."}
    else:
        return {"status": "error", "message": "User not found."}

# SECURITY CHECK

def login_required(f):
@wraps(f)
def decorated_function(*args, \*\*kwargs):
if 'user_id' not in session:
return redirect(url_for('login'))
return f(*args, \*\*kwargs)
return decorated_function

def admin_only(f):
@wraps(f)
def decorated_function(*args, \*\*kwargs):
if 'user_id' not in session or session.get('role') != 'Admin':
return redirect(url_for('pos_page'))
return f(*args, \*\*kwargs)
return decorated_function

# HOME

@app.route('/login', methods=['GET', 'POST'])
def login():
if request.method == 'POST':
username = request.form['username']
password = request.form['password']

        # Supabase Query
        response = supabase.table("system_users").select("*").eq("username", username).execute()

        if len(response.data) > 0:
            user = response.data[0]

            # Check password hash
            if check_password_hash(user['password_hash'], password) and user['is_active']:
                session['user_id'] = user['id']
                session['username'] = user['username']
                session['role'] = user['role']
                session['full_name'] = user['full_name']

                # Role-Based Redirection
                if session['role'] == 'Admin':
                    return redirect(url_for('dashboard'))
                elif session['role'] == 'Inventory':
                    return redirect(url_for('inventory_page'))
                else:
                    return redirect(url_for('pos_page'))
            else:
                return render_template('login.html', error="Invalid Password or Inactive Account!")
        else:
            return render_template('login.html', error="User not found!")

    return render_template('login.html')

# DASHBOARD ROUTE

@app.route('/dashboard')
@admin_only
def dashboard(): # 1. Check kung naka-login
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

@app.route('/inventory', methods=['GET', 'POST'])
@admin_only
def inventory_page():
try:
if request.method == 'POST':
scanned_sku = request.form.get('sku', '').strip()
item_name = request.form.get('item_name', '').strip()
brand = request.form.get('brand', '').strip()
category = request.form.get('category', '')
price = float(request.form.get('price', 0))
added_qty = int(request.form.get('stock_qty', 0))

            # 1. Check kung existing na ang SKU
            check_res = supabase.table("inventory").select("sku", "stock_qty").eq("sku", scanned_sku).execute()

            if len(check_res.data) > 0:
                # ITEM EXISTS -> Mag-a-add lang ng quantity
                current_qty = check_res.data[0]['stock_qty']
                new_qty = current_qty + added_qty

                # I-update ang status pabalik sa 'Active' kung galing sa 0
                supabase.table("inventory").update({
                    "stock_qty": new_qty,
                    "status": "Active"
                }).eq("sku", scanned_sku).execute()

                # Insert sa audit log
                supabase.table("stock_logs").insert({
                    "sku": scanned_sku,
                    "action": "Stock In",
                    "qty": added_qty,
                    "username": session.get('username'),
                    "remarks": "Inventory Auto-Update"
                }).execute()
            else:
                # NEW ITEM -> Insert new record
                supabase.table("inventory").insert({
                    "sku": scanned_sku,
                    "item_name": item_name,
                    "brand": brand,
                    "category": category,
                    "price": price,
                    "stock_qty": added_qty,
                    "status": "Active" if added_qty > 0 else "Out of Stock"
                }).execute()

            return redirect(url_for('inventory_page'))

        # GET Request -> I-display ang Inventory list
        # Hihigupin natin ang lahat ng items, naka-sort sa pinakabago
        inventory_res = supabase.table("inventory").select("*").order("item_id", desc=True).execute()
        items = inventory_res.data

        return render_template('inventory.html', items=items)

    except Exception as e:
        print(f"CRITICAL INVENTORY ERROR: {e}")
        return f"Database Error: {str(e)}"

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

    # Generate Receipt Number
    receipt_no = f"JS-{date.today().strftime('%Y%m%d')}-{int(time.time()) % 1000:03d}"

    try:
        # 1. Insert into Sales Transactions
        transaction_data = {
            "receipt_no": receipt_no,
            "total_amount": total_amount,
            "payment_method": payment_method,
            "gcash_reference": gcash_ref,
            "user_id": session['user_id']
        }
        tx_res = supabase.table("sales_transactions").insert(transaction_data).execute()

        if not tx_res.data:
            return jsonify({"status": "error", "message": "Transaction failed"}), 500

        transaction_id = tx_res.data[0]['transaction_id']

        # 2. Insert Items and Auto-Deduct Inventory
        for item in cart:
            # Insert sa sales_items
            supabase.table("sales_items").insert({
                "transaction_id": transaction_id,
                "item_description": item['name'],
                "item_type": item['type'],
                "qty": item['qty'],
                "unit_price": item['price'],
                "subtotal": (item['qty'] * item['price'])
            }).execute()

            # AUTO-DEDUCTION LOGIC (Panel Requirement)
            if item['type'] == 'Part':
                # Kunin ang current stock
                item_res = supabase.table("inventory").select("stock_qty").eq("sku", item['sku']).execute()

                if item_res.data:
                    current_qty = item_res.data[0]['stock_qty']
                    new_qty = current_qty - item['qty']

                    # Prevent negative inventory and update status
                    if new_qty <= 0:
                        new_qty = 0
                        new_status = 'Out of Stock'
                    else:
                        new_status = 'Active'

                    # Update database
                    supabase.table("inventory").update({
                        "stock_qty": new_qty,
                        "status": new_status
                    }).eq("sku", item['sku']).execute()

        # Print Receipt Logic (Direct call to your existing function)
        cashier = session.get('username', f"User {session['user_id']}")
        try:
            print_receipt_direct(receipt_no, cashier, cart, total_amount, payment_method, gcash_ref)
        except Exception as print_err:
            print(f"⚠️ Printer Error: {print_err}")

        return jsonify({"status": "success", "message": "Transaction completed!", "receipt": receipt_no})

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

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

    try:
        if request.method == 'POST':
            new_username = request.form['username']
            new_password = request.form['password']
            full_name = request.form['full_name']
            role = request.form['role']

            hashed_pw = generate_password_hash(new_password)

            supabase.table("system_users").insert({
                "username": new_username,
                "password_hash": hashed_pw,
                "full_name": full_name,
                "role": role,
                "is_active": True
            }).execute()

            return redirect(url_for('manage_users'))

        users_res = supabase.table("system_users").select("*").order("created_at", desc=True).execute()
        users = users_res.data

        return render_template('users.html', users=users)

    except Exception as e:
        return f"Database Error: {str(e)}"

@app.route('/delete_user/<string:id>', methods=['POST'])
def delete_user(id):
if 'user_id' not in session or session.get('role') != 'Admin':
return redirect(url_for('pos_page'))

    if id == str(session.get('user_id')):
        return "Error: You cannot deactivate your own active account.", 403

    try:
        supabase.table("system_users").update({
            "is_active": False
        }).eq("id", id).execute()

        return redirect(url_for('manage_users'))

    except Exception as e:
        return f"Database Error: {str(e)}"

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
printer_name = "POS-58"
date_str = datetime.now().strftime('%y-%m-%d %H:%M')

    # Fetch dynamic settings from Supabase
    settings_res = supabase.table("system_settings").select("*").limit(1).execute()
    shop_name = "Default Shop"
    shop_address = "Default Address"
    contact = "0000"
    footer = "Thank you!"

    if len(settings_res.data) > 0:
        s = settings_res.data[0]
        shop_name = s.get('shop_name', shop_name)
        shop_address = s.get('shop_address', shop_address)
        contact = s.get('contact_number', contact)
        footer = s.get('receipt_footer_message', footer)

    receipt_text = f"""

{shop_name.upper()}
{shop_address}
CP: {contact}

---

OR#: {receipt_no}
Date: {date_str}
Cashier: {cashier_name}

---

QTY ITEM AMT
"""
for item in cart:
desc = str(item['name'])[:16].ljust(16)
qty = str(item['qty']).ljust(4)
subtotal = float(item['qty']) \* float(item['price'])
amt = f"{subtotal:.2f}".rjust(8)
receipt_text += f"{qty} {desc} {amt}\n"

    receipt_text += f"""

---

TOTAL: PHP {float(total):.2f}
Method: {method}
"""
if method == 'GCash' and gcash_ref:
receipt_text += f"Ref: {gcash_ref}\n"

    receipt_text += """

---

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

    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM inventory WHERE sku = %s", (clean_sku,))
        item = cursor.fetchone()

        if item:
            if item['stock_qty'] <= 0:
                return jsonify({'status': 'error', 'message': f"Item '{item['item_name']}' is out of stock!"})

            return jsonify({
                'status': 'success',
                'data': {
                    'sku': item['sku'],
                    'item_name': item['item_name'],
                    'price': float(item['price']),
                    'stock_qty': item['stock_qty']
                }
            })
        else:
            return jsonify({'status': 'error', 'message': f'Barcode {clean_sku} not found.'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})
    finally:
        conn.close()

# UPDATE BARCODE

@app.route('/update_barcode', methods=['POST'])
def update_barcode():
if 'user_id' not in session or session.get('role') != 'Admin':
return redirect(url_for('pos_page'))

    old_sku = request.form.get('old_sku')
    new_barcode = request.form.get('new_barcode').strip()

    try:
        # 1. Check kung may ibang gumagamit na ng barcode
        check_res = supabase.table("inventory").select("*").eq("sku", new_barcode).execute()

        if len(check_res.data) > 0:
            existing_item = check_res.data[0]
            return f"Error: Barcode '{new_barcode}' is already assigned to {existing_item['item_name']}."

        # 2. Update SKU
        supabase.table("inventory").update({"sku": new_barcode}).eq("sku", old_sku).execute()
        return redirect('/inventory')

    except Exception as e:
        return f"Database Error: {str(e)}"

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

    try:
        supabase.table("inventory").update({
            "item_name": item_name,
            "brand": brand,
            "category": category,
            "price": price
        }).eq("sku", sku).execute()

        return redirect('/inventory')

    except Exception as e:
        return f"Database Error: {str(e)}"

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

@app.route('/delete_item/<string:item_sku>', methods=['POST'])
def delete_item(item_sku):
try: # Imbes na burahin, i-se-set lang natin to 'Out of Stock' at 0 quantity
response = supabase.table("inventory").update({
"status": "Out of Stock",
"quantity": 0
}).eq("sku", item_sku).execute()

        if len(response.data) > 0:
            return jsonify({"success": True, "message": "Item archived successfully (Set to Out of Stock)."})
        else:
            return jsonify({"success": False, "message": "Item not found."})

    except Exception as e:
        print(f"ERROR SA ARCHIVE: {e}")
        return jsonify({"success": False, "message": f"Database Error: {str(e)}"})

# RUN

if **name** == '**main**':
app.run(debug=True)
