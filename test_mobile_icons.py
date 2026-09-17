import os
import unittest
from app import create_app

class TestMobileIconsAndManifest(unittest.TestCase):
    def setUp(self):
        self.app = create_app({
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "SECRET_KEY": "test-secret-key",
            "SCHEDULER_AUTO_START": False,
        })
        self.client = self.app.test_client()

    def test_manifest_endpoint(self):
        """Verify /manifest.json returns valid PWA manifest JSON."""
        res = self.client.get("/manifest.json")
        self.assertEqual(res.status_code, 200)
        self.assertIn("application/manifest+json", res.headers.get("Content-Type", ""))
        data = res.get_json()
        self.assertEqual(data.get("short_name"), "Chimaera")
        self.assertEqual(data.get("display"), "standalone")
        self.assertEqual(data.get("background_color"), "#10141D")
        self.assertEqual(data.get("theme_color"), "#10141D")
        
        # Check icons defined in manifest
        icons = data.get("icons", [])
        self.assertGreaterEqual(len(icons), 4)
        purposes = {icon.get("purpose") for icon in icons}
        self.assertIn("any", purposes)
        self.assertIn("maskable", purposes)

    def test_favicon_endpoint(self):
        """Verify /favicon.ico is served directly."""
        res = self.client.get("/favicon.ico")
        self.assertEqual(res.status_code, 200)
        self.assertGreater(len(res.data), 0)

    def test_apple_touch_icon_endpoints(self):
        """Verify iOS Apple Touch Icon routes."""
        for endpoint in ["/apple-touch-icon.png", "/apple-touch-icon-precomposed.png"]:
            res = self.client.get(endpoint)
            self.assertEqual(res.status_code, 200, f"Failed on {endpoint}")
            self.assertEqual(res.headers.get("Content-Type"), "image/png")
            self.assertGreater(len(res.data), 0)

    def test_static_icon_files_accessible(self):
        """Verify all static icons declared in manifest and head are accessible."""
        icon_paths = [
            "/static/img/icon-192.png",
            "/static/img/icon-512.png",
            "/static/img/icon-maskable-192.png",
            "/static/img/icon-maskable-512.png",
            "/static/img/favicon-32.png",
            "/static/img/favicon-16.png",
            "/static/img/apple-touch-icon.png",
        ]
        for path in icon_paths:
            res = self.client.get(path)
            self.assertEqual(res.status_code, 200, f"Failed to retrieve {path}")
            self.assertEqual(res.headers.get("Content-Type"), "image/png")
            self.assertGreater(len(res.data), 0)

    def test_base_html_renders_meta_tags(self):
        """Verify base HTML template includes mobile/PWA meta and icon links."""
        res = self.client.get("/login")
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn('rel="manifest"', html)
        self.assertIn('rel="apple-touch-icon"', html)
        self.assertIn('name="apple-mobile-web-app-capable" content="yes"', html)
        self.assertIn('name="apple-mobile-web-app-title" content="Chimaera"', html)
        self.assertIn('name="theme-color" content="#10141D"', html)
        self.assertIn('apple-touch-icon.png', html)
        self.assertIn('manifest.json', html)

    def test_mobile_navigation_components(self):
        """Verify mobile navigation bar, drawer, and responsive elements."""
        from models import db, User, AllowedEmail
        with self.app.app_context():
            db.create_all()
            allowed = AllowedEmail(
                email="pilot@chimaera.mtg",
                is_admin=True,
                notes="Tactical Pilot",
                added_by="MobileTestSuite",
            )
            db.session.add(allowed)
            user = User(
                email="pilot@chimaera.mtg",
                name="Pilot",
                is_admin=True,
                is_active=True,
            )
            db.session.add(user)
            db.session.commit()
            user_id = user.id

        with self.client.session_transaction() as sess:
            sess["user_id"] = user_id
            sess["user_email"] = "pilot@chimaera.mtg"
            sess["is_admin"] = True

        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")

        # Mobile Bottom Navigation
        self.assertIn('id="mobile-bottom-nav"', html)
        self.assertIn('mobile-bottom-nav-item', html)
        self.assertIn('href="/"', html)
        self.assertIn('href="/deals"', html)
        self.assertIn('href="/deck-analyzer"', html)
        self.assertIn('href="/inventory"', html)
        self.assertIn('toggleMobileDrawer()', html)

        # Mobile Tactical Drawer & Backdrop
        self.assertIn('id="mobile-nav-drawer"', html)
        self.assertIn('id="mobile-nav-backdrop"', html)
        self.assertIn('id="btn-mobile-drawer-toggle"', html)
        self.assertIn('closeMobileDrawer()', html)
        self.assertIn('00 // Field Manual', html)
        self.assertIn('01 // Registry Dashboard', html)
        self.assertIn('02 // Priority Deals', html)
        self.assertIn('05 // Commander Hub', html)

        # Verify custom.css has mobile classes
        css_res = self.client.get("/static/css/custom.css")
        self.assertEqual(css_res.status_code, 200)
        css = css_res.data.decode("utf-8")
        self.assertIn(".safe-area-bottom", css)
        self.assertIn(".mobile-bottom-nav-item", css)
        self.assertIn(".mobile-drawer-backdrop", css)
        self.assertIn(".mobile-drawer-panel", css)

        # Verify app.js has mobile drawer functions
        js_res = self.client.get("/static/js/app.js")
        self.assertEqual(js_res.status_code, 200)
        js = js_res.data.decode("utf-8")
        self.assertIn("function openMobileDrawer()", js)
        self.assertIn("function closeMobileDrawer()", js)
        self.assertIn("function toggleMobileDrawer()", js)

if __name__ == "__main__":
    unittest.main()

