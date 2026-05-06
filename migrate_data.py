import pandas as pd
import mysql.connector

# --- DATABASE CONFIG ---
db_config = {
    'host': 'localhost',
    'user': 'root',
    'password': '',
    'database': 'js_motoworks_db'
}

def get_db_connection():
    return mysql.connector.connect(**db_config)

def generate_smart_sku(category, brand, cursor):
    cat_prefix = str(category)[:3].upper() if len(str(category)) >= 3 else str(category).upper()
    brand_prefix = str(brand)[:3].upper() if len(str(brand)) >= 3 else str(brand).upper()
    
    cursor.execute("SELECT COUNT(*) FROM inventory WHERE category = %s AND brand = %s", (category, brand))
    count = cursor.fetchone()[0]
    
    new_number = str(count + 1).zfill(3) 
    return f"{cat_prefix}-{brand_prefix}-{new_number}"

# SMART GUESSER PARA SA CATEGORY AT BRAND
def guess_category(desc):
    desc = str(desc).upper()
    if any(word in desc for word in ['OIL', 'COOLANT', 'GREASE', 'FLUID']):
        return 'Lubricants'
    elif any(word in desc for word in ['BOLT', 'NUT', 'WASHER', 'SCREW', 'TAPE', 'WIRE', 'CABLE']):
        return 'Hardware'
    elif any(word in desc for word in ['HELMET', 'COVER', 'HOLDER', 'MIRROR', 'STICKER', 'LED', 'LIGHT', 'HORN', 'SWITCH', 'ALARM']):
        return 'Accessories'
    else:
        return 'Parts'

def guess_brand(desc):
    desc = str(desc).upper()
    # Listahan ng mga sikat na brand mula sa Excel nila
    brands = ['YAMAHA', 'HONDA', 'SUZUKI', 'KAWASAKI', 'NMAX', 'AEROX', 'CLICK', 'MIO', 
              'RCB', 'HIRC', 'SMOK', 'ZENO', 'SAIYAN', 'OTAKA', 'KRX', 'MTR', 'DOMINO', 
              'BOSNY', 'DAYWAY', 'NGK', 'YAMALUBE', 'SHELL', 'PETRON', 'PIAA', 'JVT']
    for b in brands:
        if b in desc:
            return b
    return 'Generic'

def migrate_data():
    print("Reading JS Motoworks EXCEL file...")
    try:
        # skiprows=1 dahil yung tunay na header ng excel nila ay nasa row 2 pa
        df = pd.read_excel('js_inventory.xlsx', skiprows=1, engine='openpyxl') 
    except Exception as e:
        print(f"❌ Error reading file: {e}")
        return

    conn = get_db_connection()
    cursor = conn.cursor(buffered=True)

    print("Migrating and cleaning data. Please wait...")
    success_count = 0

    for index, row in df.iterrows():
        try:
            item_name = str(row['DESCRIPTION']).strip()
            # Laktawan kung blangko ang pangalan
            if item_name == 'nan' or not item_name:
                continue 

            # Kunin ang Selling Price (Gawing 0 kung walang nakalagay)
            price_val = row['Selling Cost']
            price = 0.0 if pd.isna(price_val) or str(price_val).strip() == '' else float(price_val)

            # Kunin ang Stock (Gawing 0 kung negative para iwas error sa POS)
            stock_val = row['remaining']
            stock_qty = 0 if pd.isna(stock_val) or str(stock_val).strip() == '' else int(float(stock_val))
            if stock_qty < 0:
                stock_qty = 0 

            category = guess_category(item_name)
            brand = guess_brand(item_name)
            par_level = 5

            sku = generate_smart_sku(category, brand, cursor)

            # Insert sa Inventory
            sql = "INSERT INTO inventory (sku, item_name, brand, category, price, stock_qty, par_level) VALUES (%s, %s, %s, %s, %s, %s, %s)"
            cursor.execute(sql, (sku, item_name, brand, category, price, stock_qty, par_level))
            
            # Insert sa Audit Logs
            cursor.execute(
                "INSERT INTO stock_logs (sku, action, qty, username, remarks) VALUES (%s, %s, %s, %s, %s)",
                (sku, 'Stock In', stock_qty, 'System', 'Initial EXCEL Data Migration')
            )
            success_count += 1
            print(f"✅ Migrated: {item_name} | Qty: {stock_qty} | Price: ₱{price}")
            
        except Exception as e:
            # Para kung may isang row na sira, tuloy pa rin ang pag-migrate
            print(f"⚠️ Skipping error on item '{row.get('DESCRIPTION', 'Unknown')}': {e}")

    conn.commit()
    conn.close()
    print(f"\n🎉 Migration Complete! Successfully added {success_count} items to the JS Motoworks Database.")

if __name__ == '__main__':
    migrate_data()