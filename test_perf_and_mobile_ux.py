"""
Regression Test Suite for Performance & Mobile UX Optimizations (Phases 1-4).
Covers:
- Phase 1: Lazy PEP 562 `app` binding, DB indexes, sealed WatchlistItem Scryfall bypass.
- Phase 2: Mobile P0 CSS classes (.btn-cyan, .safe-area-top), Chart.js head block,
  mobile command palette trigger, and balanced HTML in commander_hub upgrades tab.
- Phase 3: Service Worker cache versioning & /normal/ art caching, single-view SSR
  with JSON hydration payloads (#watchlist-items-json, #deals-items-json), external
  `defer` route JS bundles, Scryfall /small/ thumbnails, and small logo asset.
- Phase 4: Mobile input anti-zoom media rule, `.sticky-first-col` responsive tables,
  `inputmode="decimal"`, and `autocorrect="off"` ergonomics.
"""

import json
import os
import re
import subprocess
import sys
import unittest
from html.parser import HTMLParser
from unittest.mock import patch

from sqlalchemy import inspect as sa_inspect

from app import create_app
from models import (
    ActivityLog,
    AllowedEmail,
    MicrocenterHistory,
    MicrocenterItem,
    User,
    VendorPrice,
    WatchlistItem,
    db,
)
from providers.scryfall import ScryfallProvider

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


class _DivTagBalanceParser(HTMLParser):
    """Captures the HTML block inside a target element ID and checks div/span/button balance."""

    def __init__(self, target_id):
        super().__init__(convert_charrefs=True)
        self.target_id = target_id
        self.capturing = False
        self.done = False
        self.div_depth = 0
        self.found = False
        self.found_ids_inside = set()
        self.unclosed_stack = []

    def handle_starttag(self, tag, attrs):
        if self.done:
            return
        attr_map = dict(attrs)
        elem_id = attr_map.get("id")
        if not self.capturing and elem_id == self.target_id:
            self.capturing = True
            self.found = True
            self.div_depth = 1
            self.unclosed_stack.append(tag)
            return
        if self.capturing:
            if elem_id:
                self.found_ids_inside.add(elem_id)
            if tag == "div":
                self.div_depth += 1
            if tag not in {"input", "img", "br", "hr", "meta", "link", "source", "path", "circle", "rect", "line"}:
                self.unclosed_stack.append(tag)

    def handle_endtag(self, tag):
        if not self.capturing or self.done:
            return
        if tag == "div":
            self.div_depth -= 1
        if tag in self.unclosed_stack:
            # Pop matching tag (tolerant of void svg children)
            idx = len(self.unclosed_stack) - 1
            while idx >= 0 and self.unclosed_stack[idx] != tag:
                idx -= 1
            if idx >= 0:
                self.unclosed_stack = self.unclosed_stack[:idx]
        if self.div_depth == 0:
            self.capturing = False
            self.done = True


class _EmptyContainerParser(HTMLParser):
    """Verifies whether specific container element IDs have empty inner HTML/text."""

    def __init__(self, target_ids):
        super().__init__(convert_charrefs=True)
        self.target_ids = set(target_ids)
        self.active = []  # stack of (target_id, depth, tag)
        self.results = {}  # target_id -> list of inner tokens/tags

    def handle_starttag(self, tag, attrs):
        attr_map = dict(attrs)
        elem_id = attr_map.get("id")
        for tid, _, _ in self.active:
            self.results[tid].append(f"<{tag}>")
        if self.active:
            tid, depth, root_tag = self.active[-1]
            if tag == root_tag:
                self.active[-1] = (tid, depth + 1, root_tag)
        if elem_id in self.target_ids:
            self.results.setdefault(elem_id, [])
            self.active.append((elem_id, 1, tag))

    def handle_data(self, data):
        if data and data.strip():
            for tid, _, _ in self.active:
                self.results[tid].append(data.strip())

    def handle_endtag(self, tag):
        if not self.active:
            return
        tid, depth, root_tag = self.active[-1]
        if tag == root_tag:
            depth -= 1
            if depth == 0:
                self.active.pop()
            else:
                self.active[-1] = (tid, depth, root_tag)


class PerfAndMobileUXTestSuite(unittest.TestCase):
    """Comprehensive regression suite for Phases 1-4 optimizations."""

    @classmethod
    def setUpClass(cls):
        cls.app = create_app(
            {
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_ENGINE_OPTIONS": {},
                "DISCORD_WEBHOOK_URL": "",
                "ADMIN_EMAIL": "perf_admin@chimera.local",
            }
        )
        cls.client = cls.app.test_client()
        with cls.app.app_context():
            db.create_all()

    def setUp(self):
        with self.app.app_context():
            db.session.query(VendorPrice).delete()
            db.session.query(WatchlistItem).delete()
            db.session.query(MicrocenterHistory).delete()
            db.session.query(MicrocenterItem).delete()
            db.session.query(ActivityLog).delete()
            db.session.query(User).delete()
            db.session.query(AllowedEmail).delete()
            db.session.commit()

    def _login(self, email="perf_user@chimera.local", is_admin=True):
        with self.app.app_context():
            allowed = AllowedEmail.get_by_email(email)
            if not allowed:
                db.session.add(
                    AllowedEmail(
                        email=email,
                        is_admin=is_admin,
                        notes="Perf Test User",
                        added_by="TestSuite",
                    )
                )
                db.session.commit()
            user = User.query.filter_by(email=email).first()
            if not user:
                user = User(
                    email=email,
                    name="Perf Tester",
                    is_admin=is_admin,
                    is_active=True,
                )
                db.session.add(user)
                db.session.commit()
            uid = user.id

        with self.client.session_transaction() as sess:
            sess["user_id"] = uid
            sess["user_email"] = email
            sess["is_admin"] = is_admin
        return uid

    def test_01_lazy_pep562_app_import(self):
        """Importing `app` must not eagerly call `create_app()` until `.app` is read."""
        code = (
            "import app as app_mod\n"
            "assert app_mod._default_app is None, '_default_app should stay None before .app access'\n"
            "flask_app = app_mod.app\n"
            "assert app_mod._default_app is flask_app\n"
            "print('LAZY_OK')\n"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code],
            cwd=BASE_DIR,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(
            proc.returncode,
            0,
            msg=f"Lazy import check failed:\nSTDOUT: {proc.stdout}\nSTDERR: {proc.stderr}",
        )
        self.assertIn("LAZY_OK", proc.stdout)

    def test_02_database_performance_indexes_exist(self):
        """Verifies Phase 1 SQLAlchemy indexes on vendor_price, activity_log, microcenter tables."""
        with self.app.app_context():
            inspector = sa_inspect(db.engine)

            vp_indexes = {idx["name"] for idx in inspector.get_indexes("vendor_price")}
            self.assertIn("idx_vendor_price_watchlist_vendor", vp_indexes)

            act_indexes = {idx["name"] for idx in inspector.get_indexes("activity_log")}
            self.assertIn("idx_activity_user_created", act_indexes)
            self.assertIn("idx_activity_action_created", act_indexes)

            mc_hist_indexes = {
                idx["name"] for idx in inspector.get_indexes("microcenter_history")
            }
            self.assertIn("idx_mc_history_item_recorded", mc_hist_indexes)

            mc_item_cols = {
                tuple(idx.get("column_names") or [])
                for idx in inspector.get_indexes("microcenter_item")
            }
            # sku is unique=True index=True; store_id, name, product_id are indexed
            self.assertTrue(
                ("store_id",) in mc_item_cols or ("name",) in mc_item_cols,
                f"Expected microcenter_item indexes on store_id/name, got {mc_item_cols}",
            )

    def test_03_sealed_watchlist_skips_scryfall_lookup(self):
        """Sealed products on `/` and `/deals` must skip Scryfall lookups."""
        uid = self._login()
        with self.app.app_context():
            sealed = WatchlistItem(
                user_id=uid,
                name="Foundations Collector Booster Box",
                target_price=250.0,
                finish="sealed",
                tcgplayer_product_id=551234,
                is_any_version=False,
                set_code="FDN",
                image_uri="",
            )
            db.session.add(sealed)
            db.session.flush()
            db.session.add(
                VendorPrice(
                    watchlist_id=sealed.id,
                    vendor_name="TCGplayer",
                    price=210.0,
                    in_stock=True,
                    product_url="https://www.tcgplayer.com/product/12345",
                )
            )
            db.session.commit()

        def fail_scryfall(*args, **kwargs):
            raise AssertionError("ScryfallProvider must not be called for sealed items")

        with (
            patch.object(ScryfallProvider, "get_cards_collection", side_effect=fail_scryfall),
            patch.object(ScryfallProvider, "get_card_named", side_effect=fail_scryfall),
            patch.object(ScryfallProvider, "get_card_by_id", side_effect=fail_scryfall),
        ):
            r_home = self.client.get("/")
            self.assertEqual(r_home.status_code, 200)
            r_deals = self.client.get("/deals")
            self.assertEqual(r_deals.status_code, 200)

    def test_04_css_and_service_worker_assets(self):
        """Verifies Phase 2-4 CSS utilities and Phase 3 Service Worker rules."""
        with open(os.path.join(BASE_DIR, "static", "css", "custom.css"), "r", encoding="utf-8") as f:
            css = f.read()
        self.assertIn(".btn-cyan", css)
        self.assertIn(".safe-area-top", css)
        self.assertIn("(hover: none) and (pointer: coarse)", css)
        self.assertIn(".sticky-first-col", css)

        with open(os.path.join(BASE_DIR, "static", "sw.js"), "r", encoding="utf-8") as f:
            sw = f.read()
        self.assertIn("chimaera-static-v4", sw)
        self.assertIn("chimaera-art-v3", sw)
        self.assertIn("navigationPreload", sw)
        self.assertIn("/normal/", sw)

    def test_05_dashboard_and_deals_ssr_hydration_and_mobile_ergonomics(self):
        """Verifies `/` and `/deals` single-view SSR, thumbnails, JSON payloads, and mobile attrs."""
        uid = self._login()
        with self.app.app_context():
            card = WatchlistItem(
                user_id=uid,
                name="Sol Ring",
                target_price=5.0,
                finish="nonfoil",
                set_code="CMM",
                collector_number="410",
                image_uri="https://cards.scryfall.io/normal/front/1/2/12345.jpg",
            )
            sealed = WatchlistItem(
                user_id=uid,
                name="Modern Horizons 3 Play Booster Box",
                target_price=240.0,
                finish="sealed",
                tcgplayer_product_id=559999,
                set_code="MH3",
                image_uri="https://example.com/box.jpg",
            )
            db.session.add_all([card, sealed])
            db.session.flush()
            db.session.add_all(
                [
                    VendorPrice(
                        watchlist_id=card.id,
                        vendor_name="TCGplayer",
                        price=2.50,
                        in_stock=True,
                        product_url="https://tcgplayer.com/sol-ring",
                    ),
                    VendorPrice(
                        watchlist_id=sealed.id,
                        vendor_name="TCGplayer",
                        price=220.0,
                        in_stock=True,
                        product_url="https://tcgplayer.com/mh3-box",
                    ),
                ]
            )
            db.session.commit()

        # `/` Home Watchlist
        resp_home = self.client.get("/")
        self.assertEqual(resp_home.status_code, 200)
        html_home = resp_home.get_data(as_text=True)

        self.assertIn('id="watchlist-items-json"', html_home)
        self.assertIn("chimaera_logo_sm.webp", html_home)
        self.assertIn('id="btn-command-palette-mobile"', html_home)
        self.assertIn("https://cards.scryfall.io/small/front/1/2/12345.jpg", html_home)
        self.assertIn('inputmode="decimal"', html_home)
        self.assertIn('autocorrect="off"', html_home)

        m_json = re.search(
            r'<script id="watchlist-items-json" type="application/json">(.*?)</script>',
            html_home,
            re.DOTALL,
        )
        self.assertIsNotNone(m_json, "Expected #watchlist-items-json script payload on /")
        parsed_watchlist = json.loads(m_json.group(1))
        self.assertGreaterEqual(len(parsed_watchlist), 2)

        empty_home_parser = _EmptyContainerParser(
            ["watchlist-compact", "inperson-art-grid", "inperson-checklist"]
        )
        empty_home_parser.feed(html_home)
        for container_id in ("watchlist-compact", "inperson-art-grid", "inperson-checklist"):
            self.assertEqual(
                empty_home_parser.results.get(container_id),
                [],
                f"Expected #{container_id} to be empty on initial SSR for lazy hydration",
            )

        # `/deals` Priority Deals
        resp_deals = self.client.get("/deals")
        self.assertEqual(resp_deals.status_code, 200)
        html_deals = resp_deals.get_data(as_text=True)

        self.assertIn('id="deals-items-json"', html_deals)
        self.assertRegex(
            html_deals,
            r'<script[^>]+src="[^"]*js/deals_scanners\.js[^"]*"[^>]*defer',
        )
        self.assertIn("sticky-first-col", html_deals)
        self.assertIn('inputmode="decimal"', html_deals)
        self.assertIn('autocorrect="off"', html_deals)

        empty_deals_parser = _EmptyContainerParser(["deals-compact", "deals-swipe-track"])
        empty_deals_parser.feed(html_deals)
        self.assertEqual(
            empty_deals_parser.results.get("deals-compact"),
            [],
            "Expected #deals-compact to be empty on initial SSR for lazy hydration",
        )
        self.assertEqual(
            empty_deals_parser.results.get("deals-swipe-track"),
            [],
            "Expected #deals-swipe-track to be empty on initial SSR for lazy hydration",
        )

    def test_06_commander_hub_balance_and_secondary_route_bundles(self):
        """Verifies Chart.js on /microcenter, balanced /commander upgrades block, and external defer bundles."""
        self._login()

        # /microcenter
        r_mc = self.client.get("/microcenter")
        self.assertEqual(r_mc.status_code, 200)
        html_mc = r_mc.get_data(as_text=True)
        self.assertIn("chart.umd.min.js", html_mc)
        self.assertRegex(html_mc, r'<script[^>]+src="[^"]*js/microcenter\.js[^"]*"[^>]*defer')
        self.assertIn("sticky-first-col", html_mc)
        self.assertIn('inputmode="decimal"', html_mc)
        self.assertIn('autocorrect="off"', html_mc)

        # /commander
        r_cmdr = self.client.get("/commander")
        self.assertEqual(r_cmdr.status_code, 200)
        html_cmdr = r_cmdr.get_data(as_text=True)
        div_parser = _DivTagBalanceParser("inspectorTabContentUpgrades")
        div_parser.feed(html_cmdr)
        self.assertTrue(div_parser.found, "Missing #inspectorTabContentUpgrades on /commander")
        self.assertTrue(
            div_parser.done,
            f"#inspectorTabContentUpgrades had unclosed tags/divs (remaining div_depth={div_parser.div_depth})",
        )
        self.assertIn("combosCountBadge", div_parser.found_ids_inside)
        self.assertIn("sticky-first-col", html_cmdr)
        self.assertIn('autocorrect="off"', html_cmdr)

        # External deferred route JS bundles + autocorrect="off" across remaining routes
        expected_bundles = [
            ("/buylist", "js/buylist.js"),
            ("/timewalked", "js/timewalked.js"),
            ("/add-evaluator", "js/add_evaluator.js"),
            ("/secret-lair", "js/add_evaluator.js"),
            ("/inventory", "js/inventory.js"),
        ]
        for route, bundle in expected_bundles:
            resp = self.client.get(route)
            self.assertEqual(resp.status_code, 200, msg=f"Route {route} failed")
            body = resp.get_data(as_text=True)
            self.assertRegex(
                body,
                rf'<script[^>]+src="[^"]*{re.escape(bundle)}[^"]*"[^>]*defer',
                msg=f"Expected deferred bundle {bundle} on {route}",
            )
            self.assertIn(
                'autocorrect="off"',
                body,
                msg=f"Expected autocorrect='off' input attributes on {route}",
            )

        with open(os.path.join(BASE_DIR, "templates", "secret_lair.html"), "r", encoding="utf-8") as f:
            sl_tpl = f.read()
        self.assertIn("js/secret_lair.js", sl_tpl)
        self.assertIn("defer", sl_tpl)
        self.assertIn('autocorrect="off"', sl_tpl)


if __name__ == "__main__":
    unittest.main()
