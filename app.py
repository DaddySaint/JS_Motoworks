from flask import Flask, jsonify, render_template, request, redirect, url_for, session, send_file
from werkzeug.security import generate_password_hash, check_password_hash
import os
import time
import requests
import win32print
import pandas as pd
from io import BytesIO
from datetime import date, datetime, timedelta
from dotenv import load_dotenv
from functools import wraps
from supabase import create_client, Client

def compute_status(stock_qty, par_level=5):
    stock_qty = int(stock_qty or 0)
    par_level = int(par_level or 0)

    if stock_qty <= 0:
        return "Out of Stock"
    elif stock_qty <= par_level:
        return "Low Stock"
    return "In Stock"


def now_iso():
    return datetime.utcnow().isoformat()

# ==========================================
# 1. SETUP & INITIALIZATION
# ==========================================
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
PAYMONGO_SECRET_KEY = os.getenv('PAYMONGO_SECRET_KEY')

app = Flask(__name__)
app.secret_key = 'js_motoworks_super_secret_key'


# ==========================================
# 2. SECURITY & ROLE DECORATORS
# ==========================================
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


# ==========================================
# 3. AUTHENTICATION & LOGIN
# ==========================================
@app.route('/')
def index():
    if 'user_id' in session:
        role = session.get('role', 'Cashier')
        if role == 'Admin':
            return redirect(url_for('dashboard'))
        elif role == 'Inventory':
            return redirect(url_for('inventory_page'))
        else:
            return redirect(url_for('pos_page'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('index'))

    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        try:
            # Query the 'users' table
            response = supabase.table("users").select("*").eq("username", username).execute()

            if len(response.data) > 0:
                user = response.data[0]
                if check_password_hash(user['password_hash'], password) and user['is_active']:
                    session.clear()
                    session['user_id'] = user['user_id']
                    session['username'] = user['username']
                    session['role'] = user['role']
                    session['full_name'] = user['full_name']

                    if user['role'] == 'Customer':
                        return redirect(url_for('customer_portal'))
                    
                    return redirect(url_for('index'))
                else:
                    return render_template('login.html', error="Invalid Password or Account Deactivated!")
            else:
                return render_template('login.html', error="User not found!")
        except Exception as e:
            return render_template('login.html', error=f"Database connection error: {str(e)}")

    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# ==========================================
# 4. DASHBOARD (Fixed JSON Error)
# ==========================================
@app.route('/dashboard')
@admin_only
def dashboard():
    try:
        # 1. Total Sales Today
        today_str = date.today().isoformat()
        sales_res = supabase.table("sales_transactions").select("total_amount", "transaction_date").execute()
        sales_today = 0
        if sales_res.data:
            for s in sales_res.data:
                if s.get('transaction_date', '').startswith(today_str):
                    sales_today += float(s['total_amount'])

        # 2. Low Stock Count
        inv_res = supabase.table("inventory").select("stock_qty", "par_level").execute()
        low_stock = sum(1 for item in inv_res.data if item['stock_qty'] <= item['par_level']) if inv_res.data else 0

        # 3. Recent Transactions
        recent_sales = supabase.table("sales_transactions").select("*").order("transaction_date", desc=True).limit(5).execute().data or []

        # 4. Chart Data Fallbacks (To prevent Jinja2 JSON errors)
        chart_dates = []
        chart_sales = []
        pay_labels = []
        pay_data = []

        return render_template('dashboard.html',
                               sales_today=sales_today,
                               low_stock=low_stock,
                               recent_sales=recent_sales,
                               chart_dates=chart_dates,
                               chart_sales=chart_sales,
                               pay_labels=pay_labels,
                               pay_data=pay_data)
    except Exception as e:
        print(f"Dashboard Error: {e}")
        return render_template('dashboard.html', sales_today=0, low_stock=0, recent_sales=[], chart_dates=[], chart_sales=[], pay_labels=[], pay_data=[])


# ==========================================
# 5. INVENTORY & AUDIT LOGS
# ==========================================
@app.route('/inventory', methods=['GET', 'POST'])
@admin_only
def inventory_page():
    try:
        if request.method == 'POST':
            scanned_sku = request.form.get('sku', '').strip()
            barcode = request.form.get('barcode', '').strip()
            item_name = request.form.get('item_name', '').strip()
            brand = request.form.get('brand', '').strip()
            category = request.form.get('category', '').strip()
            specs = request.form.get('specs', '').strip()
            price = float(request.form.get('price', 0) or 0)
            added_qty = int(request.form.get('stock_qty', 0) or 0)
            par_level = int(request.form.get('par_level', 5) or 5)

            if not scanned_sku:
                return "SKU is required.", 400

            check_res = (
                supabase.table("inventory")
                .select("item_id, sku, stock_qty, par_level")
                .eq("sku", scanned_sku)
                .execute()
            )

            if check_res.data:
                current_item = check_res.data[0]
                current_qty = int(current_item.get("stock_qty", 0) or 0)
                current_par = int(current_item.get("par_level", par_level) or par_level)

                new_qty = current_qty + added_qty
                new_status = compute_status(new_qty, current_par)

                supabase.table("inventory").update({
                    "stock_qty": new_qty,
                    "status": new_status,
                    "last_updated": now_iso()
                }).eq("sku", scanned_sku).execute()

                # Audit log for stock in existing item
                supabase.table("stock_logs").insert({
                    "sku": scanned_sku,
                    "action": "Stock In",
                    "qty": added_qty,
                    "username": session.get('username'),
                    "remarks": "Added via Form"
                }).execute()

            else:
                new_status = compute_status(added_qty, par_level)

                supabase.table("inventory").insert({
                    "sku": scanned_sku,
                    "barcode": barcode if barcode else None,
                    "item_name": item_name,
                    "brand": brand if brand else None,
                    "category": category if category else None,
                    "specs": specs if specs else None,
                    "price": price,
                    "stock_qty": added_qty,
                    "par_level": par_level,
                    "status": new_status,
                    "last_updated": now_iso()
                }).execute()

                # Audit log for newly created item
                supabase.table("stock_logs").insert({
                    "sku": scanned_sku,
                    "action": "Stock In",
                    "qty": added_qty,
                    "username": session.get('username'),
                    "remarks": "New inventory item added"
                }).execute()

            return redirect(url_for('inventory_page'))

        items = supabase.table("inventory").select("*").order("item_id", desc=True).execute().data or []
        return render_template('inventory.html', items=items)

    except Exception as e:
        return f"Database Error: {str(e)}"

@app.route('/delete_item/<string:item_sku>', methods=['POST'])
@admin_only
def delete_item(item_sku):
    try:
        response = supabase.table("inventory").update({
            "status": "Out of Stock",
            "stock_qty": 0,
            "last_updated": now_iso()
        }).eq("sku", item_sku).execute()

        if response.data:
            return jsonify({"success": True, "message": "Item archived successfully."})
        return jsonify({"success": False, "message": "Item not found."})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})

@app.route('/edit_item', methods=['POST'])
@admin_only
def edit_item():
    try:
        item_id = int(request.form.get('item_id'))
        sku = request.form.get('sku', '').strip()
        barcode = request.form.get('barcode', '').strip()
        item_name = request.form.get('item_name', '').strip()
        brand = request.form.get('brand', '').strip()
        category = request.form.get('category', '').strip()
        specs = request.form.get('specs', '').strip()
        price = float(request.form.get('price', 0) or 0)
        stock_qty = int(request.form.get('stock_qty', 0) or 0)
        par_level = int(request.form.get('par_level', 5) or 5)

        if not sku:
            return "SKU is required.", 400

        # Check if another item already uses this SKU
        sku_check = (
            supabase.table("inventory")
            .select("item_id")
            .eq("sku", sku)
            .neq("item_id", item_id)
            .execute()
        )
        if sku_check.data:
            return "SKU already exists for another item.", 400

        # Check barcode uniqueness if barcode is provided
        if barcode:
            barcode_check = (
                supabase.table("inventory")
                .select("item_id")
                .eq("barcode", barcode)
                .neq("item_id", item_id)
                .execute()
            )
            if barcode_check.data:
                return "Barcode already exists for another item.", 400

        status = compute_status(stock_qty, par_level)

        supabase.table("inventory").update({
            "sku": sku,
            "barcode": barcode if barcode else None,
            "item_name": item_name,
            "brand": brand if brand else None,
            "category": category if category else None,
            "specs": specs if specs else None,
            "price": price,
            "stock_qty": stock_qty,
            "par_level": par_level,
            "status": status,
            "last_updated": now_iso()
        }).eq("item_id", item_id).execute()

        return redirect(url_for('inventory_page'))

    except Exception as e:
        return f"Database Error: {str(e)}"
    

@app.route('/restock', methods=['POST'])
@admin_only
def restock_item():
    try:
        sku = request.form['sku'].strip()
        added_qty = int(request.form['added_qty'])
        current_user = session.get('username')

        item_res = (
            supabase.table("inventory")
            .select("stock_qty, par_level")
            .eq("sku", sku)
            .execute()
        )

        if item_res.data:
            current_qty = int(item_res.data[0]['stock_qty'] or 0)
            par_level = int(item_res.data[0].get('par_level', 5) or 5)

            new_qty = current_qty + added_qty
            new_status = compute_status(new_qty, par_level)

            supabase.table("inventory").update({
                "stock_qty": new_qty,
                "status": new_status,
                "last_updated": now_iso()
            }).eq("sku", sku).execute()

            supabase.table("stock_logs").insert({
                "sku": sku,
                "action": "Restock",
                "qty": added_qty,
                "username": current_user,
                "remarks": "Manual Restock"
            }).execute()

        return redirect(url_for('inventory_page'))

    except Exception as e:
        return f"Database Error: {str(e)}"

@app.route('/audit_logs')
@admin_only
def audit_logs():
    logs = supabase.table("stock_logs").select("*").order("log_date", desc=True).execute().data or []
    return render_template('audit_logs.html', logs=logs)


# ==========================================
# 6. POS & CHECKOUT
# ==========================================
@app.route('/pos')
@login_required
def pos_page():
    services = supabase.table("services").select("*").execute().data or []
    return render_template('pos.html', services=services)

@app.route('/api/get_item/<sku>', methods=['GET'])
def get_item(sku):
    clean_sku = sku.strip()
    try:
        item_res = supabase.table("inventory").select("*").eq("sku", clean_sku).execute()
        if not item_res.data:
            return jsonify({'status': 'error', 'message': f'Barcode {clean_sku} not found.'})
            
        item = item_res.data[0]
        if item['stock_qty'] <= 0:
            return jsonify({'status': 'error', 'message': f"Item '{item['item_name']}' is out of stock!"})

        return jsonify({
            'status': 'success',
            'data': {'sku': item['sku'], 'item_name': item['item_name'], 'price': float(item['price']), 'stock_qty': item['stock_qty']}
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})

@app.route('/checkout', methods=['POST'])
def checkout():
    if 'user_id' not in session:
        return jsonify({"status": "error", "message": "Unauthorized"}), 401

    data = request.json
    cart = data.get('cart')
    payment_method = data.get('payment_method')
    total_amount = data.get('total_amount')
    gcash_ref = data.get('gcash_ref')

    receipt_no = f"JS-{date.today().strftime('%Y%m%d')}-{int(time.time()) % 1000:03d}"

    try:
        # 1. Create Transaction
        tx_res = supabase.table("sales_transactions").insert({
            "receipt_no": receipt_no,
            "cashier_id": session['user_id'],
            "total_amount": total_amount,
            "payment_method": payment_method,
            "gcash_reference": gcash_ref,
            "user_id": session['user_id']
        }).execute()
        
        if not tx_res.data:
            return jsonify({"status": "error", "message": "Transaction failed"}), 500
            
        transaction_id = tx_res.data[0]['transaction_id']

        # 2. Insert Items & Auto-Deduct Inventory
        for item in cart:
            supabase.table("sales_items").insert({
                "transaction_id": transaction_id,
                "item_description": item['name'],
                "item_type": item['type'],
                "qty": item['qty'],
                "unit_price": item['price'],
                "subtotal": (item['qty'] * item['price'])
            }).execute()

if item['type'] == 'Part':
    item_data = (
        supabase.table("inventory")
        .select("stock_qty, par_level")
        .eq("sku", item['sku'])
        .execute()
    )

    if item_data.data:
        current_qty = int(item_data.data[0]['stock_qty'] or 0)
        par_level = int(item_data.data[0].get('par_level', 5) or 5)

        new_qty = max(0, current_qty - int(item['qty']))
        new_status = compute_status(new_qty, par_level)

        supabase.table("inventory").update({
            "stock_qty": new_qty,
            "status": new_status,
            "last_updated": now_iso()
        }).eq("sku", item['sku']).execute()

        cashier = session.get('username', f"User {session['user_id']}")
        try:
            print_receipt_direct(receipt_no, cashier, cart, total_amount, payment_method, gcash_ref)
        except Exception as e:
            print(f"Printer error: {e}")

        return jsonify({"status": "success", "message": "Transaction completed!", "receipt": receipt_no})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ==========================================
# 7. BOOKING & APPOINTMENTS
# ==========================================
@app.route('/book', methods=['GET', 'POST'])
def book_appointment():
    if request.method == 'POST':
        service = request.form['service_category']
        notes = request.form.get('customer_notes', '')
        if notes: service += f" (Issue: {notes})"

        supabase.table("appointments").insert({
            "customer_name": request.form['customer_name'],
            "contact_number": request.form['contact_number'],
            "motorcycle_model": request.form['motorcycle_model'],
            "service_requested": service,
            "appointment_date": request.form['appointment_date'],
            "appointment_time": request.form['appointment_time']
        }).execute()
        return render_template('book_success.html', name=request.form['customer_name'])
    return render_template('booking.html')

@app.route('/track_booking', methods=['GET', 'POST'])
def track_booking():
    status_data = None
    search_contact = ""
    if request.method == 'POST':
        search_contact = request.form.get('contact_number').strip()
        status_data = supabase.table('appointments').select('*').eq('contact_number', search_contact).order('appointment_date', desc=True).execute().data
    return render_template('track_booking.html', status_data=status_data, contact=search_contact)

@app.route('/appointments')
@admin_only
def appointments_page():
    appts = supabase.table("appointments").select("*").order("appointment_date", desc=True).execute().data or []
    return render_template('appointments.html', appointments=appts)

@app.route('/update_appointment/<int:id>', methods=['POST'])
@admin_only
def update_appointment(id):
    supabase.table("appointments").update({"status": request.form['status']}).eq("appointment_id", id).execute()
    return redirect(url_for('appointments_page'))

@app.route('/delete_appointment/<int:id>', methods=['POST'])
@admin_only
def delete_appointment(id):
    supabase.table("appointments").delete().eq("appointment_id", id).execute()
    return redirect(url_for('appointments_page'))


# ==========================================
# 8. SALES HISTORY & REPORTS
# ==========================================
@app.route('/sales')
@login_required
def sales_history():
    sales = supabase.table("sales_transactions").select("*").order("transaction_date", desc=True).execute().data or []
    total_sales = sum(float(s['total_amount']) for s in sales if not s.get('is_archived', False))
    return render_template('sales_history.html', sales=sales, total_sales=total_sales)

@app.route('/export_sales_excel')
@admin_only
def export_sales_excel():
    tx_res = supabase.table("sales_transactions").select("*").execute().data or []
    items_res = supabase.table("sales_items").select("*").execute().data or []

    df_transactions = pd.DataFrame(tx_res)
    df_items = pd.DataFrame(items_res)

    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df_transactions.to_excel(writer, index=False, sheet_name='Transactions')
        df_items.to_excel(writer, index=False, sheet_name='Items')

    output.seek(0)
    return send_file(output, download_name=f"Sales_Report_{date.today()}.xlsx", as_attachment=True)


# ==========================================
# 9. USER MANAGEMENT & SETTINGS
# ==========================================
@app.route('/users', methods=['GET', 'POST'])
@admin_only
def manage_users():
    if request.method == 'POST':
        hashed_pw = generate_password_hash(request.form['password'])
        supabase.table("users").insert({
            "username": request.form['username'],
            "password_hash": hashed_pw,
            "full_name": request.form['full_name'],
            "role": request.form['role'],
            "is_active": True
        }).execute()
        return redirect(url_for('manage_users'))

    users_list = supabase.table("users").select("*").order("created_at", desc=True).execute().data or []
    return render_template('users.html', users=users_list)

@app.route('/delete_user/<int:id>', methods=['POST'])
@admin_only
def delete_user(id):
    if id == session.get('user_id'):
        return "Error: You cannot deactivate your own account.", 403
    supabase.table("users").update({"is_active": False}).eq("user_id", id).execute()
    return redirect(url_for('manage_users'))


# ==========================================
# 10. RECEIPT PRINTER UTILITY
# ==========================================
def print_receipt_direct(receipt_no, cashier_name, cart, total, method, gcash_ref):
    printer_name = "POS-58"
    date_str = datetime.now().strftime('%y-%m-%d %H:%M')

    settings = supabase.table("system_settings").select("*").limit(1).execute().data
    s = settings[0] if settings else {}
    shop_name = s.get('shop_name', 'JS Motoworks')
    shop_address = s.get('shop_address', 'Makati City')
    contact = s.get('contact_number', '0900-000-0000')

    receipt_text = f"\n{shop_name.upper()}\n{shop_address}\nCP: {contact}\n--------------------------------\nOR#: {receipt_no}\nDate: {date_str}\nCashier: {cashier_name}\n--------------------------------\nQTY   ITEM               AMT\n"
    
    for item in cart:
        desc = str(item['name'])[:16].ljust(16)
        qty = str(item['qty']).ljust(4)
        amt = f"{(float(item['qty']) * float(item['price'])):.2f}".rjust(8)
        receipt_text += f"{qty} {desc} {amt}\n"

    receipt_text += f"--------------------------------\nTOTAL: PHP {float(total):.2f}\nMethod: {method}\n"
    if method == 'GCash' and gcash_ref:
        receipt_text += f"Ref: {gcash_ref}\n"
    receipt_text += "--------------------------------\nTHANK YOU! RIDE SAFE!\n\n\n"

    try:
        hprinter = win32print.OpenPrinter(printer_name)
        hjob = win32print.StartDocPrinter(hprinter, 1, ("Receipt", None, "RAW"))
        win32print.StartPagePrinter(hprinter)
        win32print.WritePrinter(hprinter, receipt_text.encode('utf-8'))
        win32print.EndPagePrinter(hprinter)
        win32print.EndDocPrinter(hprinter)
        win32print.ClosePrinter(hprinter)
    except Exception as e:
        print(f"Printer Error: {e}")

@app.route('/api/generate_paymongo_link', methods=['POST'])
def generate_paymongo_link():
    amount_in_cents = int(float(request.json.get('total_amount')) * 100)
    url = "https://api.paymongo.com/v1/links"
    payload = {"data": {"attributes": {"amount": amount_in_cents, "description": "POS Transaction"}}}
    
    try:
        res = requests.post(url, json=payload, auth=(PAYMONGO_SECRET_KEY, '')).json()
        return jsonify({"status": "success", "checkout_url": res['data']['attributes']['checkout_url']})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

# ==========================================
# CUSTOMER SIGN-UP
# ==========================================
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        full_name = request.form['full_name']
        
        try:
            # Check kung may kaparehong username
            check_user = supabase.table("users").select("*").eq("username", username).execute()
            if len(check_user.data) > 0:
                return render_template('register.html', error="Username already exists!")

            hashed_pw = generate_password_hash(password)
            
            # I-save bilang Customer role
            supabase.table("users").insert({
                "username": username,
                "password_hash": hashed_pw,
                "full_name": full_name,
                "role": "Customer",
                "is_active": True
            }).execute()
            
            return redirect(url_for('login', msg="Registration Successful! Please login."))
        except Exception as e:
            return render_template('register.html', error=f"Error: {str(e)}")

    return render_template('register.html')

# ==========================================
# CUSTOMER PORTAL (Status Tracker)
# ==========================================
@app.route('/customer_portal')
@login_required
def customer_portal():
    if session.get('role') != 'Customer':
        return redirect(url_for('index'))
    
    # Kukunin lang ang booking na ginawa ng naka-login na customer
    my_bookings = supabase.table("appointments").select("*").eq("user_id", session['user_id']).order("appointment_date", desc=True).execute().data or []
    
    return render_template('customer_portal.html', bookings=my_bookings)


if __name__ == '__main__':
    app.run(debug=True)