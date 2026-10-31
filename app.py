import os, sqlite3, secrets, re, time, logging, smtplib, mimetypes, hmac, hashlib, struct, base64, io
from decimal import Decimal
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, abort, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from email.message import EmailMessage

# Read a local .env file for developer convenience; platform environment variables win.
_ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.isfile(_ENV_PATH):
    with open(_ENV_PATH, encoding="utf-8") as _env_file:
        for _entry in _env_file:
            _entry = _entry.strip()
            if _entry and not _entry.startswith("#") and "=" in _entry:
                _env_key, _env_value = _entry.split("=", 1)
                os.environ.setdefault(_env_key.strip(), _env_value.strip().strip("\"'"))

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("DATABASE_PATH", os.path.join(APP_DIR, "store.db"))
app = Flask(__name__)
IS_PRODUCTION = os.environ.get("APP_ENV", "development").lower() == "production"
SECRET_KEY = os.environ.get("SECRET_KEY")
if IS_PRODUCTION and not SECRET_KEY:
    raise RuntimeError("SECRET_KEY must be configured when APP_ENV=production")
app.secret_key = SECRET_KEY or secrets.token_hex(32)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_SECURE=IS_PRODUCTION, PERMANENT_SESSION_LIFETIME=28800, MAX_CONTENT_LENGTH=20*1024*1024)
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger("shopeasy")

def send_store_email(to_email, subject, body, order_id=None, event="system"):
    """Send transactional email when SMTP is configured; always record the attempt."""
    host=os.environ.get("SMTP_HOST",""); sender=os.environ.get("SMTP_FROM",os.environ.get("STORE_EMAIL",""))
    result="not_configured"
    if host and sender and to_email:
        try:
            msg=EmailMessage(); msg["Subject"]=subject; msg["From"]=sender; msg["To"]=to_email; msg.set_content(body)
            port=int(os.environ.get("SMTP_PORT","587")); user=os.environ.get("SMTP_USER",""); password=os.environ.get("SMTP_PASSWORD","")
            if os.environ.get("SMTP_USE_SSL","false").lower()=="true":
                with smtplib.SMTP_SSL(host,port,timeout=15) as smtp:
                    if user: smtp.login(user,password)
                    smtp.send_message(msg)
            else:
                with smtplib.SMTP(host,port,timeout=15) as smtp:
                    smtp.starttls()
                    if user: smtp.login(user,password)
                    smtp.send_message(msg)
            result="sent"
        except Exception as exc:
            logger.exception("Transactional email failed"); result="failed: "+type(exc).__name__
    con=db()
    con.execute("INSERT INTO email_logs(order_id,recipient,subject,event,result) VALUES(?,?,?,?,?)",(order_id,to_email or "",subject,event,result)); con.commit(); con.close()
    return result

def make_email_code(): return f"{secrets.randbelow(1000000):06d}"

def store_uploads(files, product_id):
    upload_dir=os.path.join(APP_DIR,"static","uploads"); os.makedirs(upload_dir,exist_ok=True)
    saved=[]
    for file in files:
        if not file or not file.filename: continue
        ext=os.path.splitext(secure_filename(file.filename))[1].lower()
        if ext not in {".jpg",".jpeg",".png",".webp",".gif"}: raise ValueError("Only JPG, PNG, WEBP and GIF images are accepted.")
        data=file.read(8*1024*1024+1); file.stream.seek(0)
        if len(data)>8*1024*1024: raise ValueError("Each product image must be 8 MB or smaller.")
        signatures={".jpg":b"\xff\xd8\xff", ".jpeg":b"\xff\xd8\xff", ".png":b"\x89PNG\r\n\x1a\n", ".gif":b"GIF8", ".webp":b"RIFF"}
        if ext in signatures and not data.startswith(signatures[ext]): raise ValueError("An uploaded file does not match its image type.")
        if ext==".webp" and data[8:12]!=b"WEBP": raise ValueError("Invalid WEBP image.")
        filename=secrets.token_hex(16)+ext; dest=os.path.join(upload_dir,filename)
        with open(dest,"wb") as out: out.write(data)
        saved.append("/static/uploads/"+filename)
    if saved:
        con=db()
        for i,path in enumerate(saved): con.execute("INSERT INTO product_images(product_id,image_path,sort_order) VALUES(?,?,?)",(product_id,path,i))
        con.execute("UPDATE products SET image=? WHERE id=? AND (image IS NULL OR image='')",(saved[0],product_id))
        con.commit(); con.close()
    return saved

def db():
    con = sqlite3.connect(DB_PATH, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=15000")
    return con

def init_db():
    con = db()
    con.executescript("""
    CREATE TABLE IF NOT EXISTS users (
      id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL,
      password TEXT NOT NULL, is_admin INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS products (
      id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, category TEXT NOT NULL DEFAULT 'General',
      description TEXT DEFAULT '', price REAL NOT NULL, stock INTEGER NOT NULL DEFAULT 0,
      image TEXT DEFAULT '', active INTEGER DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS orders (
      id INTEGER PRIMARY KEY AUTOINCREMENT, order_code TEXT UNIQUE NOT NULL, customer_name TEXT NOT NULL,
      email TEXT NOT NULL, phone TEXT NOT NULL, address TEXT NOT NULL, city TEXT NOT NULL,
      items TEXT NOT NULL, total REAL NOT NULL, status TEXT NOT NULL DEFAULT 'Pending',
      created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS contact_messages (
      id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, email TEXT NOT NULL,
      subject TEXT NOT NULL, message TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS login_attempts (
      id INTEGER PRIMARY KEY AUTOINCREMENT, ip TEXT NOT NULL, attempted_at INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS product_images (id INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE, image_path TEXT NOT NULL, sort_order INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS email_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER, recipient TEXT NOT NULL, subject TEXT NOT NULL, event TEXT NOT NULL, result TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS customer_otps (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL, code_hash TEXT NOT NULL, expires_at INTEGER NOT NULL, attempts INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS order_events (id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER NOT NULL REFERENCES orders(id), status TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS order_items (
      id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
      product_id INTEGER, product_name TEXT NOT NULL, quantity INTEGER NOT NULL CHECK(quantity > 0),
      unit_price REAL NOT NULL
    );
    """)
    con.execute("INSERT OR IGNORE INTO schema_migrations(version) VALUES(1)")
    # Add account security columns safely for existing SQLite databases.
    user_cols={r[1] for r in con.execute("PRAGMA table_info(users)")}
    if "two_factor_enabled" not in user_cols: con.execute("ALTER TABLE users ADD COLUMN two_factor_enabled INTEGER DEFAULT 1")
    order_cols={r[1] for r in con.execute("PRAGMA table_info(orders)")}
    if "user_id" not in order_cols: con.execute("ALTER TABLE orders ADD COLUMN user_id INTEGER")
    # Remove only the known demo account if it still has the published demo password.
    demo_admin=con.execute("SELECT id,password FROM users WHERE email='admin@shopeasy.local'").fetchone()
    if demo_admin and check_password_hash(demo_admin["password"], "ChangeMe123!"):
        con.execute("DELETE FROM users WHERE id=?", (demo_admin["id"],))
    admin_email=os.environ.get("ADMIN_EMAIL","").strip().lower()
    admin_password=os.environ.get("ADMIN_PASSWORD","")
    if admin_email and admin_password:
        if len(admin_password)<14: raise RuntimeError("ADMIN_PASSWORD must be at least 14 characters")
        if not con.execute("SELECT id FROM users WHERE email=?",(admin_email,)).fetchone():
            con.execute("INSERT INTO users(name,email,password,is_admin) VALUES(?,?,?,1)",("Store Administrator",admin_email,generate_password_hash(admin_password)))
    # Product catalogue is managed by the administrator; never seed placeholder business data.
    con.commit()
    con.close()

def admin_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("admin_login"))
        return fn(*args, **kwargs)
    return wrapped

@app.before_request
def csrf_guard():
    if request.method in {"POST","PUT","PATCH","DELETE"}:
        expected=session.get("_csrf_token","")
        submitted=request.form.get("csrf_token","")
        if not expected or not secrets.compare_digest(submitted,expected): abort(400, description="Invalid or missing CSRF token.")

@app.context_processor
def inject_security_and_contact():
    if "_csrf_token" not in session: session["_csrf_token"]=secrets.token_urlsafe(32)
    return {"csrf_token":session["_csrf_token"], "store_name":os.environ.get("STORE_NAME","ShopEasy"), "store_email":os.environ.get("STORE_EMAIL",""), "store_phone":os.environ.get("STORE_PHONE",""), "store_whatsapp":os.environ.get("STORE_WHATSAPP",""), "store_hours":os.environ.get("STORE_HOURS",""), "store_address":os.environ.get("STORE_ADDRESS","")}

@app.after_request
def security_headers(response):
    response.headers.setdefault("X-Content-Type-Options","nosniff")
    response.headers.setdefault("X-Frame-Options","DENY")
    response.headers.setdefault("Referrer-Policy","strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy","camera=(), microphone=(), geolocation=()")
    response.headers.setdefault("Content-Security-Policy", "default-src 'self' https: data:; img-src 'self' https: data:; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; script-src 'self' 'unsafe-inline'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'")
    return response

@app.context_processor
def cart_count():
    return {"cart_count": sum(int(v) for v in session.get("cart", {}).values())}

@app.route("/")
def home():
    # The storefront homepage is a landing page, not the product catalogue.
    con=db()
    categories=con.execute("SELECT DISTINCT category FROM products WHERE active=1 ORDER BY category").fetchall()
    featured=con.execute("SELECT * FROM products WHERE active=1 ORDER BY id DESC LIMIT 4").fetchall()
    con.close()
    return render_template("home.html",categories=categories,featured=featured,q="")

@app.route("/shop", strict_slashes=False)
@app.route("/electronics", strict_slashes=False)
def shop():
    q=request.args.get("q","").strip()[:100]
    selected_category=request.args.get("category","").strip()[:80]
    sort=request.args.get("sort","featured")
    sort_map={"featured":"id DESC","price_asc":"price ASC","price_desc":"price DESC","name":"name COLLATE NOCASE ASC"}
    order_sql=sort_map.get(sort,sort_map["featured"])
    clauses=["active=1"]; params=[]
    if selected_category:
        clauses.append("category=?"); params.append(selected_category)
    if q:
        clauses.append("(name LIKE ? OR category LIKE ?)"); params.extend([f"%{q}%",f"%{q}%"])
    con=db()
    products=con.execute("SELECT * FROM products WHERE "+" AND ".join(clauses)+" ORDER BY "+order_sql,params).fetchall()
    categories=con.execute("SELECT DISTINCT category FROM products WHERE active=1 ORDER BY category").fetchall()
    price_range=con.execute("SELECT MIN(price) AS low, MAX(price) AS high FROM products WHERE active=1").fetchone()
    con.close()
    return render_template("shop.html",products=products,categories=categories,q=q,selected_category=selected_category,sort=sort,price_range=price_range)

@app.route("/category/<category>")
def category(category):
    return redirect(url_for("shop",category=category))

@app.route("/product/<int:product_id>")
def product(product_id):
    con=db(); item=con.execute("SELECT * FROM products WHERE id=? AND active=1",(product_id,)).fetchone()
    gallery=con.execute("SELECT image_path FROM product_images WHERE product_id=? ORDER BY sort_order,id",(product_id,)).fetchall(); con.close()
    if not item: abort(404)
    images=[r["image_path"] for r in gallery]
    if item["image"] and item["image"] not in images: images.insert(0,item["image"])
    return render_template("product.html", product=item, images=images)

@app.route("/cart")
def cart():
    cart_data=session.get("cart",{})
    con=db()
    items=[]; total=0
    for pid, qty in cart_data.items():
        p=con.execute("SELECT * FROM products WHERE id=? AND active=1",(pid,)).fetchone()
        if p:
            line=float(p["price"])*int(qty); total+=line
            items.append({"product":p,"qty":int(qty),"line":line})
    con.close()
    return render_template("cart.html",items=items,total=total)

@app.post("/cart/add/<int:product_id>")
def add_cart(product_id):
    con=db(); p=con.execute("SELECT * FROM products WHERE id=? AND active=1",(product_id,)).fetchone(); con.close()
    if not p: abort(404)
    cart=session.get("cart",{})
    key=str(product_id)
    try: requested_qty=int(request.form.get("qty",1))
    except (TypeError,ValueError): abort(400)
    if requested_qty<1 or requested_qty>99: abort(400)
    new_qty=int(cart.get(key,0))+requested_qty
    if new_qty > p["stock"]:
        flash("Sorry, that quantity is more than the available stock.","error")
    else:
        cart[key]=new_qty; session["cart"]=cart; flash("Product added to your cart.","success")
    return redirect(request.referrer or url_for("home"))

@app.post("/cart/update")
def update_cart():
    cart=session.get("cart",{})
    con=db()
    for key in list(cart.keys()):
        try:
            qty=int(request.form.get("qty_"+key,0))
            if qty<=0: cart.pop(key,None)
            elif qty>99: flash("Quantity cannot exceed 99 per item.","error")
            else:
                p=con.execute("SELECT stock FROM products WHERE id=? AND active=1",(key,)).fetchone()
                if not p or p["stock"]<=0: cart.pop(key,None)
                else: cart[key]=min(qty,p["stock"])
        except (TypeError,ValueError): flash("An invalid quantity was ignored.","error")
    con.close()
    session["cart"]=cart
    flash("Your cart has been updated.","success")
    return redirect(url_for("cart"))

@app.route("/checkout",methods=["GET","POST"])
def checkout():
    cart_data=session.get("cart",{})
    if not cart_data:
        flash("Your cart is empty.","error"); return redirect(url_for("home"))
    con=db(); items=[]; total=Decimal("0.00")
    for pid,qty in cart_data.items():
        p=con.execute("SELECT * FROM products WHERE id=? AND active=1",(pid,)).fetchone()
        if p:
            if int(qty)>p["stock"]:
                flash(f"Stock changed for {p['name']}. Please update your cart.","error"); con.close(); return redirect(url_for("cart"))
            line=Decimal(str(p["price"]))*int(qty); total+=line; items.append((p,int(qty),line))
    if request.method=="POST":
        name=request.form.get("name","").strip(); email=request.form.get("email","").strip().lower()
        phone=request.form.get("phone","").strip(); address=request.form.get("address","").strip(); city=request.form.get("city","").strip()
        email_ok=re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+",email or "")
        if not all([name,email,phone,address,city]) or not email_ok or len(name)>120 or len(email)>254 or len(phone)>40 or len(address)>500 or len(city)>100:
            flash("Enter all delivery details and a valid email address.","error")
        else:
            try:
                con.execute("BEGIN IMMEDIATE"); verified=[]; verified_total=Decimal("0.00")
                for pid,qty in cart_data.items():
                    p=con.execute("SELECT * FROM products WHERE id=? AND active=1",(pid,)).fetchone()
                    if not p or int(qty)<1 or int(qty)>int(p["stock"]): raise ValueError("Stock changed. Please review your cart.")
                    line=Decimal(str(p["price"]))*int(qty); verified_total+=line; verified.append((p,int(qty),line))
                if not verified: raise ValueError("Your cart no longer contains available products.")
                order_code="SE-"+secrets.token_hex(4).upper()
                summary=" | ".join([f"{p['name']} x{qty}" for p,qty,line in verified])
                con.execute("INSERT INTO orders(order_code,customer_name,email,phone,address,city,items,total,user_id) VALUES(?,?,?,?,?,?,?,?,?)",(order_code,name,email,phone,address,city,summary,str(verified_total),session.get("customer_id")))
                order_id=con.execute("SELECT last_insert_rowid()").fetchone()[0]
                for p,qty,line in verified:
                    con.execute("INSERT INTO order_items(order_id,product_id,product_name,quantity,unit_price) VALUES(?,?,?,?,?)",(order_id,p["id"],p["name"],qty,str(p["price"])))
                    changed=con.execute("UPDATE products SET stock=stock-? WHERE id=? AND stock>=?",(qty,p["id"],qty)).rowcount
                    if changed!=1: raise ValueError("Stock changed while placing your order. Please try again.")
                con.commit(); con.close(); session["cart"]={}
                send_store_email(email, f"ShopEasy order {order_code} confirmed", f"Hello {name},\n\nYour order {order_code} has been placed successfully.\nOrder total: Rs. {verified_total}\nPayment: Cash on Delivery.\nWe will email you when it is dispatched and when it is out for delivery.\n\nThank you for shopping with ShopEasy.", order_id, "order_placed")
                return render_template("success.html",order_code=order_code,total=verified_total)
            except (ValueError,sqlite3.Error) as exc:
                con.rollback(); logger.warning("Checkout transaction rejected: %s",str(exc))
                flash(str(exc) if isinstance(exc,ValueError) else "We could not place your order. Please try again.","error")
    con.close(); return render_template("checkout.html",items=items,total=total)



@app.route("/account/register",methods=["GET","POST"])
def customer_register():
    if request.method=="POST":
        name=request.form.get("name","").strip(); email=request.form.get("email","").strip().lower(); password=request.form.get("password","")
        if not name or len(name)>120 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+",email) or len(password)<12 or len(password)>256:
            flash("Enter a valid name/email and a password of at least 12 characters.","error")
        else:
            con=db()
            try:
                con.execute("INSERT INTO users(name,email,password,is_admin,two_factor_enabled) VALUES(?,?,?,0,1)",(name,email,generate_password_hash(password))); con.commit(); flash("Account created. Please sign in to verify your email.","success"); return redirect(url_for("customer_login"))
            except sqlite3.IntegrityError: flash("An account with that email already exists.","error")
            finally: con.close()
    return render_template("customer_auth.html",mode="register")

@app.route("/account/login",methods=["GET","POST"])
def customer_login():
    if request.method=="POST":
        email=request.form.get("email","").strip().lower(); password=request.form.get("password","")
        con=db(); user=con.execute("SELECT * FROM users WHERE email=? AND is_admin=0",(email,)).fetchone(); con.close()
        if user and check_password_hash(user["password"],password):
            code=make_email_code(); digest=hashlib.sha256((app.secret_key+code).encode()).hexdigest(); con=db()
            con.execute("DELETE FROM customer_otps WHERE email=?",(email,)); con.execute("INSERT INTO customer_otps(email,code_hash,expires_at) VALUES(?,?,?)",(email,digest,int(time.time())+600)); con.commit(); con.close()
            session["pending_customer_email"]=email
            send_store_email(email,"Your ShopEasy sign-in verification code",f"Your one-time sign-in code is {code}. It expires in 10 minutes. If you did not request this, ignore this email.",None,"customer_login_otp")
            flash("A six-digit verification code has been sent to your email. SMTP must be configured to receive it.","success"); return redirect(url_for("customer_verify"))
        flash("Email or password was not correct.","error")
    return render_template("customer_auth.html",mode="login")

@app.route("/account/verify",methods=["GET","POST"])
def customer_verify():
    email=session.get("pending_customer_email")
    if not email: return redirect(url_for("customer_login"))
    if request.method=="POST":
        code=request.form.get("code","").strip(); con=db(); otp=con.execute("SELECT * FROM customer_otps WHERE email=? ORDER BY id DESC LIMIT 1",(email,)).fetchone()
        if otp and otp["expires_at"]>=int(time.time()) and otp["attempts"]<5 and hmac.compare_digest(otp["code_hash"],hashlib.sha256((app.secret_key+code).encode()).hexdigest()):
            user=con.execute("SELECT id,name FROM users WHERE email=? AND is_admin=0",(email,)).fetchone(); con.execute("DELETE FROM customer_otps WHERE email=?",(email,)); con.commit(); con.close()
            saved_cart=session.get("cart",{})
            session.clear(); session["cart"]=saved_cart; session["customer_id"]=user["id"]; session["customer_name"]=user["name"]; session["customer_email"]=email; session.permanent=True
            return redirect(url_for("customer_account"))
        if otp: con.execute("UPDATE customer_otps SET attempts=attempts+1 WHERE id=?",(otp["id"],)); con.commit()
        con.close(); flash("That code is invalid or expired. Please try again.","error")
    return render_template("customer_auth.html",mode="verify",email=email)

@app.route("/account")
def customer_account():
    if not session.get("customer_id"): return redirect(url_for("customer_login"))
    con=db(); orders=con.execute("SELECT * FROM orders WHERE user_id=? ORDER BY id DESC",(session["customer_id"],)).fetchall(); con.close()
    return render_template("customer_account.html",orders=orders)

@app.post("/account/logout")
def customer_logout():
    session.pop("customer_id",None); session.pop("customer_name",None); session.pop("customer_email",None); return redirect(url_for("home"))

@app.route("/admin/workspace")
@admin_required
def admin_workspace():
    return render_template("admin_workspace.html")

@app.route("/admin/login",methods=["GET","POST"])
def admin_login():
    if request.method=="POST":
        email=request.form.get("email","").lower().strip(); password=request.form.get("password","")
        ip=request.remote_addr or "unknown"; now=int(time.time()); con=db()
        con.execute("DELETE FROM login_attempts WHERE attempted_at<?",(now-900,))
        attempts=con.execute("SELECT COUNT(*) FROM login_attempts WHERE ip=? AND attempted_at>=?",(ip,now-900)).fetchone()[0]
        user=con.execute("SELECT * FROM users WHERE email=? AND is_admin=1",(email,)).fetchone()
        if attempts>=8:
            con.close(); flash("Too many login attempts. Please wait 15 minutes.","error")
        elif user and check_password_hash(user["password"],password):
            con.execute("DELETE FROM login_attempts WHERE ip=?",(ip,)); con.commit(); con.close()
            session.clear(); session["admin"]=True; session["admin_email"]=email; session.permanent=True
            return redirect(url_for("admin"))
        else:
            con.execute("INSERT INTO login_attempts(ip,attempted_at) VALUES(?,?)",(ip,now)); con.commit(); con.close()
            flash("Login details were not correct.","error")
    return render_template("admin_login.html")


@app.post("/admin/logout")
@admin_required
def admin_logout():
    session.pop("admin",None); session.pop("admin_email",None)
    return redirect(url_for("home"))

@app.route("/admin", strict_slashes=False)
@app.route("/admin/<section>", strict_slashes=False)
@admin_required
def admin(section="overview"):
    allowed_sections={"overview","products","orders","inventory","analytics","messages","email-tracking"}
    section=section.replace("_","-")
    if section not in allowed_sections: abort(404)
    con=db()
    products=con.execute("SELECT * FROM products ORDER BY id DESC").fetchall()
    orders=con.execute("SELECT * FROM orders ORDER BY id DESC").fetchall()
    contact_messages=con.execute("SELECT * FROM contact_messages ORDER BY id DESC LIMIT 100").fetchall()
    email_logs=con.execute("SELECT * FROM email_logs ORDER BY id DESC LIMIT 100").fetchall()
    active_products = [p for p in products if p["active"]]
    valid_orders = [o for o in orders if o["status"] != "Cancelled"]
    stats={"products":len(active_products),"orders":len(orders),"revenue":sum(float(o["total"]) for o in valid_orders),
           "pending":sum(1 for o in orders if o["status"] in ("Pending", "Confirmed")),
           "low_stock":sum(1 for p in active_products if int(p["stock"]) <= 5)}
    chart_rows = con.execute("""SELECT date(created_at) AS day, SUM(total) AS revenue FROM orders
                              WHERE status!='Cancelled' AND date(created_at)>=date('now','-6 days')
                              GROUP BY date(created_at) ORDER BY day""").fetchall()
    chart_map = {r["day"]: float(r["revenue"] or 0) for r in chart_rows}
    from datetime import date, timedelta
    sales_chart = [{"label": (date.today()-timedelta(days=i)).strftime("%a"),
                    "value": chart_map.get((date.today()-timedelta(days=i)).isoformat(), 0)} for i in range(6,-1,-1)]
    max_sales = max([x["value"] for x in sales_chart] + [1])
    con.close()
    return render_template("admin.html",products=products,orders=orders,contact_messages=contact_messages,email_logs=email_logs,stats=stats,sales_chart=sales_chart,max_sales=max_sales,current_section=section)

@app.post("/admin/product/add")
@admin_required
def add_product():
    name=request.form.get("name","").strip(); category=request.form.get("category","General").strip()
    description=request.form.get("description","").strip()
    image=request.form.get("image","").strip()
    try: price=float(request.form.get("price",0)); stock=int(request.form.get("stock",0))
    except ValueError: flash("Price and stock must be numbers.","error"); return redirect(url_for("admin"))
    if not name or len(name)>160 or len(category)>80 or len(description)>2000 or not 0<=price<=100000000 or not 0<=stock<=1000000 or (image and not re.match(r"^https?://",image,re.I)):
        flash("Enter valid product details. Image URLs must use http or https.","error"); return redirect(url_for("admin"))
    con=db(); con.execute("INSERT INTO products(name,category,description,price,stock,image) VALUES(?,?,?,?,?,?)",(name,category or "General",description,price,stock,image)); pid=con.execute("SELECT last_insert_rowid()").fetchone()[0]; con.commit(); con.close()
    try: store_uploads(request.files.getlist("images"),pid)
    except ValueError as exc: flash(str(exc),"error")
    flash("Product added.","success"); return redirect(url_for("admin"))

@app.post("/admin/product/<int:pid>/edit")
@admin_required
def edit_product(pid):
    name=request.form.get("name","").strip(); category=request.form.get("category","General").strip() or "General"
    description=request.form.get("description","").strip(); image=request.form.get("image","").strip()
    try: price=float(request.form.get("price","")); stock=int(request.form.get("stock",""))
    except (TypeError,ValueError): flash("Price and stock must be valid numbers.","error"); return redirect(url_for("admin"))
    if not name or len(name)>160 or len(category)>80 or len(description)>2000 or not 0<=price<=100000000 or not 0<=stock<=1000000 or (image and not re.match(r"^https?://",image,re.I)):
        flash("Please review the product fields and image URL.","error"); return redirect(url_for("admin"))
    con=db(); changed=con.execute("UPDATE products SET name=?,category=?,description=?,price=?,stock=?,image=? WHERE id=?",(name,category,description,price,stock,image,pid)).rowcount; con.commit(); con.close()
    if not changed: abort(404)
    flash("Product updated.","success"); return redirect(url_for("admin"))

@app.post("/admin/product/<int:pid>/delete")
@admin_required
def delete_product(pid):
    con=db(); con.execute("UPDATE products SET active=0 WHERE id=?",(pid,)); con.commit(); con.close()
    flash("Product hidden from the store.","success"); return redirect(url_for("admin"))

@app.post("/admin/order/<int:oid>/status")
@admin_required
def order_status(oid):
    status=request.form.get("status")
    allowed=["Pending","Confirmed","Processing","Shipped","Out for delivery","Delivered","Cancelled"]
    if status not in allowed: abort(400)
    con=db()
    try:
        con.execute("BEGIN IMMEDIATE")
        order=con.execute("SELECT status,email,customer_name,order_code,total FROM orders WHERE id=?",(oid,)).fetchone()
        if not order: con.rollback(); abort(404)
        previous=order["status"]
        lines=con.execute("SELECT product_id,quantity FROM order_items WHERE order_id=?",(oid,)).fetchall()
        if status=="Cancelled" and previous in ("Shipped", "Delivered"):
            raise ValueError("Shipped or delivered orders must be handled through the returns process.")
        if status=="Cancelled" and previous!="Cancelled":
            for line in lines:
                if line["product_id"] is not None:
                    con.execute("UPDATE products SET stock=stock+? WHERE id=?",(line["quantity"],line["product_id"]))
        elif previous=="Cancelled" and status!="Cancelled":
            for line in lines:
                if line["product_id"] is not None:
                    updated=con.execute("UPDATE products SET stock=stock-? WHERE id=? AND active=1 AND stock>=?",(line["quantity"],line["product_id"],line["quantity"])).rowcount
                    if updated!=1: raise ValueError("Cannot reopen this order: one or more items no longer have enough stock.")
        con.execute("UPDATE orders SET status=? WHERE id=?",(status,oid))
        con.execute("INSERT INTO order_events(order_id,status) VALUES(?,?)",(oid,status))
        con.commit()
        email_data=dict(order)
        flash("Order status updated.","success")
        if status!=previous and status in ("Confirmed","Processing","Shipped","Out for delivery","Delivered","Cancelled"):
            subjects={"Confirmed":"Your ShopEasy order is confirmed","Processing":"Your ShopEasy order is being prepared","Shipped":"Your ShopEasy order has been dispatched","Out for delivery":"Your ShopEasy order is out for delivery — please keep payment ready","Delivered":"Your ShopEasy order has been delivered","Cancelled":"Your ShopEasy order was cancelled"}
            bodies={"Confirmed":"Your order has been confirmed.","Processing":"We are preparing your items.","Shipped":"Your order has been dispatched.","Out for delivery":"Your parcel is near your location and is out for delivery. Please keep the Cash on Delivery amount of Rs. {total} ready.","Delivered":"Your order is marked delivered. Thank you for shopping with us.","Cancelled":"Your order has been cancelled."}
            send_store_email(email_data["email"],subjects[status],f"Hello {email_data['customer_name']},\n\n{bodies[status].format(total=email_data['total'])}\nOrder: {email_data['order_code']}\n\nShopEasy",oid,"status_"+status.lower().replace(" ","_"))
    except ValueError as exc:
        con.rollback(); flash(str(exc),"error")
    finally:
        con.close()
    return redirect(url_for("admin"))

@app.route("/about")
def about(): return render_template("info.html",title="About us",body="Welcome to your new online store. Replace this text with your business story.")

@app.route("/contact",methods=["GET","POST"])
def contact():
    if request.method=="POST":
        name=request.form.get("name","").strip(); email=request.form.get("email","").strip().lower()
        subject=request.form.get("subject","").strip(); message=request.form.get("message","").strip()
        if not name or len(name)>120 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+",email or "") or not subject or len(subject)>160 or not message or len(message)>3000:
            flash("Please complete all fields using a valid email address.","error")
        else:
            con=db(); con.execute("INSERT INTO contact_messages(name,email,subject,message) VALUES(?,?,?,?)",(name,email,subject,message)); con.commit(); con.close()
            flash("Your message has been received. The store team can review it in the admin area.","success"); return redirect(url_for("contact"))
    return render_template("info.html",title="Contact us",body="Contact the ShopEasy team.")

@app.errorhandler(500)
def internal_error(e):
    logger.exception("Unhandled application error: %s", e)
    return render_template("info.html", title="Something went wrong", body="We could not complete that request. Please try again."), 500

@app.errorhandler(404)
def not_found(e): return render_template("info.html",title="Page not found",body="Sorry, the page you requested could not be found."),404

@app.route("/admin/export/orders")
@admin_required
def export_orders():
    import csv, io
    from flask import Response
    con=db()
    rows=con.execute("SELECT order_code,customer_name,email,phone,city,address,items,total,status,created_at FROM orders ORDER BY id DESC").fetchall()
    con.close()
    output=io.StringIO()
    writer=csv.writer(output)
    writer.writerow(["Order", "Customer", "Email", "Phone", "City", "Address", "Items", "Total PKR", "Status", "Created"])
    for row in rows:
        safe_row=[]
        for value in list(row):
            cell="" if value is None else str(value)
            if cell.startswith(("=", "+", "-", "@", "\t", "\r")):
                cell="'"+cell
            safe_row.append(cell)
        writer.writerow(safe_row)
    return Response("\ufeff"+output.getvalue(), mimetype="text/csv", headers={"Content-Disposition":"attachment; filename=shopeasy-orders.csv"})

init_db()

if __name__=="__main__":
    app.run(host=os.environ.get("HOST","127.0.0.1"),port=int(os.environ.get("PORT","5000")),debug=False)
