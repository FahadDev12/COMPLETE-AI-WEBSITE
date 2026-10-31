"""Flask integration smoke tests. Run: python -m unittest discover -s tests -v"""
import os, re, tempfile, unittest, sqlite3
_TEMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = os.path.join(_TEMP.name, "test-store.db")
os.environ["ADMIN_EMAIL"] = "owner@example.test"
os.environ["ADMIN_PASSWORD"] = "Test-Admin-Password-2026!"
os.environ["SECRET_KEY"] = "test-only-secret-key-not-for-production"
import app as shop

class ShopEasySmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        shop.app.config.update(TESTING=True)
        with sqlite3.connect(shop.DB_PATH) as con:
            con.execute("INSERT INTO products(name,category,description,price,stock,image) VALUES(?,?,?,?,?,?)", ("Smoke Test Device","Testing","test item",10.00,10,""))

    def setUp(self):
        self.client = shop.app.test_client()

    def token(self, path="/"):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        match = re.search(rb'name="csrf_token" value="([^"]+)"', response.data)
        self.assertIsNotNone(match, "Rendered form page must expose CSRF token")
        return match.group(1).decode()

    def db(self):
        return sqlite3.connect(shop.DB_PATH)

    def login(self):
        token = self.token("/admin/login")
        response = self.client.post("/admin/login", data={"csrf_token":token,"email":"owner@example.test","password":"Test-Admin-Password-2026!"}, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Welcome back", response.data)

    def test_public_pages(self):
        for path in ("/", "/about", "/contact", "/cart", "/admin/login"):
            self.assertEqual(self.client.get(path).status_code, 200, path)

    def test_csrf_is_required(self):
        response = self.client.post("/contact", data={"name":"x","email":"x@y.com","subject":"x","message":"x"})
        self.assertEqual(response.status_code, 400)

    def test_contact_message_is_persisted(self):
        token = self.token("/contact")
        response = self.client.post("/contact", data={"csrf_token":token,"name":"Test","email":"test@example.com","subject":"Help","message":"Need help"})
        self.assertEqual(response.status_code, 302)
        with self.db() as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM contact_messages").fetchone()[0], 1)

    def test_admin_can_create_edit_and_hide_product(self):
        self.login(); token=self.token("/admin")
        self.client.post("/admin/product/add", data={"csrf_token":token,"name":"QA Item","category":"QA","price":"12.50","stock":"4","description":"test","image":""})
        with self.db() as con:
            pid=con.execute("SELECT id FROM products WHERE name='QA Item'").fetchone()[0]
        token=self.token("/admin")
        response=self.client.post(f"/admin/product/{pid}/edit",data={"csrf_token":token,"name":"QA Item Updated","category":"QA","price":"15","stock":"5","description":"edited","image":""})
        self.assertEqual(response.status_code,302)
        token=self.token("/admin")
        self.assertEqual(self.client.post(f"/admin/product/{pid}/delete",data={"csrf_token":token}).status_code,302)
        with self.db() as con:
            self.assertEqual(con.execute("SELECT active FROM products WHERE id=?",(pid,)).fetchone()[0],0)

    def test_checkout_stock_and_cancel_transition(self):
        with self.db() as con:
            pid, stock = con.execute("SELECT id,stock FROM products WHERE active=1 AND stock>0 LIMIT 1").fetchone()
        product=self.client.get(f"/product/{pid}"); self.assertEqual(product.status_code,200)
        token=self.token(f"/product/{pid}")
        self.client.post(f"/cart/add/{pid}",data={"csrf_token":token,"qty":"1"})
        token=self.token("/checkout")
        response=self.client.post("/checkout",data={"csrf_token":token,"name":"Buyer","email":"buyer@example.com","phone":"03001234567","address":"1 Main Road","city":"Karachi"})
        self.assertEqual(response.status_code,200)
        with self.db() as con:
            order_id=con.execute("SELECT id FROM orders ORDER BY id DESC LIMIT 1").fetchone()[0]
            after=con.execute("SELECT stock FROM products WHERE id=?",(pid,)).fetchone()[0]
        self.assertEqual(after,stock-1)
        self.login(); token=self.token("/admin")
        self.client.post(f"/admin/order/{order_id}/status",data={"csrf_token":token,"status":"Cancelled"})
        with self.db() as con:
            restored=con.execute("SELECT stock FROM products WHERE id=?",(pid,)).fetchone()[0]
        self.assertEqual(restored,stock)

    def test_security_headers_and_logout_method(self):
        response=self.client.get("/")
        self.assertEqual(response.headers.get("X-Frame-Options"),"DENY")
        self.assertIn("Content-Security-Policy",response.headers)
        self.assertEqual(self.client.get("/admin/logout").status_code,405)

if __name__ == "__main__": unittest.main()
