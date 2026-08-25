# Chimaera // MTG Market Surveillance 🛸

**Chimaera** is a clinical, tactical, and production-ready Magic: The Gathering market surveillance and price intelligence web application. Inspired by tactical intelligence dashboards, it monitors single card prices across **TCGplayer** (via Scryfall), **MightyMeeple.com** (via BinderPOS/Shopify backend), and **eBay**, alerting collectors via **Discord Webhooks** when prices drop below customized target acquisition thresholds.

---

### 🎨 Visual Style Specification
- **Design Philosophy:** Minimalist, tactical, clinical, authoritative. Inspired by tactical intelligence dashboards with high-density data legibility.
- **Color Palette (Dark Mode):**
  - **Background:** Deep Tactical Blue-Gray (`#10141D`)
  - **Surface:** Bridge Gray (`#1B2230`)
  - **Typography:** Crisp Slate-100 (`#F1F5F9`) & Slate-400 (`#94A3B8`)
  - **Primary Accent:** Tactical Crimson (`#DC143C`) — Buttons & Deal Alerts
  - **Secondary Accent:** Cyan / Teal (`#00CED1`) — Links & Telemetry
- **Typography:**
  - **Headings & Primary UI:** Modern Sans-Serif (`Montserrat`, `Inter`)
  - **Data/Telemetry:** Monospace (`Fira Code`, `Roboto Mono`)
- **Shapes & Aesthetics:** Sharp corners, geometric grids, thin precise 1px border lines, and clinical linear iconography.

---

## 🛠️ Architecture & Tech Stack

- **Backend:** Python 3.11+ using **Flask** and **SQLAlchemy**.
- **Database:** **Neon Serverless PostgreSQL** connected via `psycopg2-binary` and SQLAlchemy.
  - Connection pooling configured with `pool_pre_ping=True` and `pool_recycle=300` to smoothly handle Neon's auto-suspend/resume behavior without dropped connections.
  - Automatic zero-config fallback to local **SQLite** (`sqlite:///chimera.db`) when `DATABASE_URL` is omitted in development.
- **Frontend:** Jinja2 templates styled with Tailwind CSS, custom Google Fonts (`Montserrat`, `Inter`, `Fira Code`), real-time Scryfall search autocomplete, set and finish selectors, and tactical deal indicators.
- **Background Jobs:** **APScheduler** running automated price surveillance checks on a configurable interval (every 6 to 12 hours).
- **Price & Inventory Providers:**
  1. **Scryfall API:** Card name autocomplete, printing resolution, high-resolution artwork, oracle metadata, and TCGplayer Market USD prices.
  2. **Mighty Meeple:** Live stock and variant scanner querying Mighty Meeple's Shopify/BinderPOS backend (`search/suggest.json` and `/products/{handle}.js`) for condition tiers (NM, LP) and in-stock pricing.
  3. **eBay:** MTG singles scanner supporting the official eBay Finding/Browse API with an HTML scraping fallback for Buy-It-Now listings.
- **Deal Engine & Notifications:** Automated deal evaluator that triggers rich Discord Webhook embeds when an in-stock card price drops to or below the target threshold.

---

## 📂 Project Structure

```text
Chimera/
├── app.py                  # Flask web factory, API endpoints & template routes
├── worker.py               # Standalone daemon/cron worker for background surveillance
├── config.py               # Central environment variable & database connection config
├── models.py               # SQLAlchemy models (User, AllowedEmail, WatchlistItem, VendorPrice, ActivityLog, SystemSetting)
├── deal_engine.py          # Multi-vendor deal aggregator & Discord dispatcher
├── providers/
│   ├── __init__.py
│   ├── scryfall.py         # Scryfall REST client & lowest price calculator across printings
│   ├── mightymeeple.py     # Mighty Meeple Shopify/BinderPOS inventory scanner
│   └── ebay.py             # eBay MTG single listings search & price extractor
├── templates/
│   ├── base.html           # Tactical layout with navigation, modals, and toast alerts
│   ├── index.html          # Registry dashboard with multi-vendor price comparison matrix
│   ├── deals.html          # Dedicated priority active deals view
│   ├── admin.html          # Administrative console, cards surveillance & security audit
│   ├── login.html          # Authentication portal
│   └── access_denied.html  # Access restricted security notice
├── static/
│   ├── img/
│   │   └── chimaera_logo.jpg # Chimaera wireframe sigil
│   ├── css/
│   │   └── custom.css      # Tactical theme variables, tactical utilities & fonts
│   └── js/
│       └── app.js          # Autocomplete debounce, dynamic print loader, and async API calls
├── Procfile                # Multi-process definition (web + worker dynos)
├── start_worker.ps1        # PowerShell standalone worker start script
├── start_worker.bat        # Windows batch standalone worker launcher
├── requirements.txt        # Python package dependencies
├── .env.example            # Example environment configuration
├── .env                    # Local environment variables
└── README.md               # Documentation & setup guide
```

---

## 🚀 Getting Started

### 1. Prerequisites
- Python 3.11 or higher
- (Optional) A free [Neon](https://neon.tech) Serverless PostgreSQL database
- (Optional) A Discord channel webhook URL for deal alerts

### 2. Installation & Setup

1. **Clone or navigate to the repository directory:**
   ```bash
   cd Chimera
   ```

2. **Create and activate a virtual environment (optional but recommended):**
   ```bash
   python -m venv venv
   # On Windows:
   venv\Scripts\activate
   # On Linux/macOS:
   source venv/bin/activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure environment variables:**
   Copy `.env.example` to `.env` (or edit existing `.env`):
   ```bash
   cp .env.example .env
   ```

---

## ⚙️ Configuration (`.env`)

| Variable | Description | Default |
|---|---|---|
| `PORT` | Port number for the web application | `5050` |
| `DATABASE_URL` | Neon PostgreSQL connection URI (`postgresql://user:pass@ep-xyz.../neondb?sslmode=require`) | `sqlite:///chimera.db` |
| `SECRET_KEY` | Flask session secret key | `chimera-dev-secret-key` |
| `DISCORD_WEBHOOK_URL` | Discord Webhook URL for real-time deal alerts | `""` (Disabled) |
| `POLL_INTERVAL_HOURS` | Frequency of background APScheduler price scans | `6` |
| `EBAY_APP_ID` | Optional eBay Developer App ID for official Finding API | `""` |

### Neon PostgreSQL Setup
To connect to Neon:
1. Create a project at [neon.tech](https://neon.tech).
2. Copy your connection string from the Neon dashboard.
3. Paste it into `.env`:
   ```env
   DATABASE_URL=postgresql://neondb_owner:npg_xxxx@ep-sample-123456.us-east-2.aws.neon.tech/neondb?sslmode=require
   ```
4. Chimera's SQLAlchemy engine automatically applies `pool_pre_ping=True` and `pool_recycle=300` to sustain healthy connections across Neon's serverless auto-suspend lifecycle.
5. On app startup, `db.create_all()` automatically builds all necessary tables and indexes.

---

## 💻 Running the Application

### Option A: Using PowerShell Scripts (Recommended on Windows)

Start the application and launch the dashboard in your default browser:
```powershell
.\start_app.ps1
```

Stop the server and release the network port:
```powershell
.\stop_app.ps1
```

---

### Option B: Manual Command Line

Start the development server:
```bash
python app.py
```

Then open your browser and navigate to:
```
http://localhost:5050
```

---

## 🎯 Features & Usage

### 1. Adding Cards to Your Watchlist (Single & Bulk Import)
- **Single Acquisition:** Click **"Acquire"** in the navigation bar. Type any MTG card name into the search bar with instant Scryfall autocomplete, choose Any Version or a specific printing, set a target threshold, assign an optional deck/purpose tag (select existing or type new), and save.
- **Bulk Target Import:** Click **"Bulk Add"** in the top navigation or dashboard toolbar. Paste a semicolon-separated list of MTG card names (e.g., `"Simic Growth Chamber; Tangled Islet; Rimewood Falls; Evolving Wilds; Lush Oasis; Thornwood Falls; Lonely Sandbar; Tranquil Thicket; Bant Panorama"`). Chimaera batch-resolves canonical metadata via Scryfall's `/cards/collection` endpoint, applies optional 1-click deal presets (-10% / -20%), applies batch tags to all imported assets, deduplicates against your active watchlist, and initiates immediate multi-vendor market price checks.

### 2. Card Tagging, Deck Categorization & Filter Matrix
- Categorize targets by deck (e.g., *"Atraxa Commander"*, *"Modern Burn"*) or purpose (*"Cube"*, *"Trade Target"*).
- Pick from existing tags using 1-click quick-select pills or browser autocomplete, or type a new tag.
- Interactive tag badges on cards allow 1-click instant filtering of your target registry.
- Filter the registry by specific tags or isolate untagged cards using the toolbar dropdown.

### 3. Multi-Vendor Price Comparison Matrix
Each card on the Wishlist dashboard displays live pricing from:
- **TCGplayer:** Current Market Price and direct checkout link.
- **Mighty Meeple:** In-stock status (`In Stock` / `Out of Stock`), condition tier (NM/LP), price, and direct product link.
- **eBay:** Lowest Buy-It-Now listing price and direct search link.

### 4. Deal Detection & Active Deals View
- When any vendor's in-stock price is **≤ Target Price**, the card is marked with a **🔥 Deal Found** badge displaying the dollar and percentage savings.
- Visit the **Active Deals** tab (`/deals`) to see a curated list of all active deals.

### 5. Discord Deal Notifications
When a card drops below your target price, Chimera automatically dispatches a rich Discord Webhook embed containing:
- High-res card artwork
- Target price vs best live deal price
- Dollar and percentage savings
- Multi-vendor price comparison table
- Direct 1-click checkout URL

### 5. Automated Background Polling
APScheduler runs in the background to re-poll prices across all storefronts every `POLL_INTERVAL_HOURS` hours without requiring manual intervention. You can also trigger an immediate refresh using the **"Refresh All"** button or individual card **"Poll"** buttons.

---

## 🧪 Testing the Modules

Run automated verification tests on Scryfall, Mighty Meeple, and the deal engine:
```bash
python -c "from providers import ScryfallProvider, MightyMeepleProvider; s = ScryfallProvider(); print('Scryfall Autocomplete:', s.autocomplete('Black Lotus')); m = MightyMeepleProvider(); print('Mighty Meeple Sample:', m.search_card('Lightning Bolt'))"
```

---

## 📄 License
MIT License. Magic: The Gathering is a trademark of Wizards of the Coast LLC. Chimera is not affiliated with Wizards of the Coast, Scryfall, Mighty Meeple, eBay, or TCGplayer.
