# Swagbucks Multi-Account Balance Monitor

A personal, multi-account Swagbucks SB balance tracking and monitoring bot built with **Python**, **Playwright (Async API)**, **asyncio**, **SQLite**, and **Rich**.

---

## 🌟 Key Features

- **Isolated Persistent Sessions**: Each account operates inside its own browser profile (`browser_sessions/account_X/`). Cookies and storage are strictly isolated.
- **Single Manual Authentication**: Log in manually once per account in a real browser. Solves CAPTCHA/MFA naturally without storing or handling passwords.
- **Concurrent & Asynchronous Monitoring**: All accounts are polled simultaneously every 60 seconds (or custom interval) using `asyncio.gather`.
- **Live Terminal Dashboard**: Clean, real-time dashboard displaying individual balances, delta changes, total combined SB, and a countdown timer.
- **Fault-Tolerant Error Handling**: If an account session expires or fails, other accounts continue monitoring uninterrupted.
- **Historical SQLite Persistence**: Stores all checks, balances, and changes in `data/sb_history.db`.
- **Strictly Passive Monitoring**: Does NOT perform any surveys, clicks, searches, earning activities, or automated bypasses.

---

## 📁 Project Structure

```text
swagbucks_multi_tracker/
│
├── web_app.py              # Web Dashboard server (FastAPI + WebSocket/Polling)
├── main.py                 # Terminal interactive CLI & live monitor
├── account_manager.py      # Session manager & interactive login handler
├── monitor.py              # Playwright async concurrent balance polling
├── database.py             # SQLite data access, historical logs & stats
├── dashboard.py            # Rich terminal UI with countdown & tables
├── requirements.txt        # Dependencies
├── .gitignore              # Ignores sessions, database, & environment secrets
├── README.md               # Setup and documentation
│
├── templates/              # Web Dashboard frontend
│   └── index.html          # Modern dark-mode responsive web interface
│
├── browser_sessions/       # Persistent browser data folders (Auto-created, git-ignored)
│   ├── account_1/
│   ├── account_2/
│   └── ...
│
└── data/                   # SQLite database directory (Auto-created, git-ignored)
    └── sb_history.db
```

---

## 🛠️ Windows & VS Code Setup Instructions

### Prerequisites
- **Python 3.10+** installed on Windows.
- **VS Code** with Python extension installed.

---

### Step 1: Open the Project in VS Code
1. Launch **VS Code**.
2. Go to **File -> Open Folder...** and select:
   ```text
   C:\Users\pasum\swagbucks_multi_tracker
   ```
3. Open a terminal in VS Code:
   - Press `` Ctrl + ` `` (Backtick) or go to **Terminal -> New Terminal**.

---

### Step 2: Activate the Virtual Environment

In the VS Code terminal (PowerShell), run:

```powershell
.\.venv\Scripts\activate
```

---

### Step 3: Run the Application

You can use either the **Web Dashboard Website** or the **Terminal Console**:

#### 🌐 Option A: Run the Web Dashboard (Recommended)
```powershell
python web_app.py
```
Open your browser and navigate to:
👉 **`http://localhost:8080`** *(or `http://127.0.0.1:8080`)*

*(Note: If your browser forces HTTPS, use `https://localhost:8000`)*

From this website you can:
- View live balances, today's net change, and total SB.
- Click **"➕ Add Account"** to launch the browser and log in.
- Click **"🔄 Check Now"** for an immediate simultaneous check.
- View real-time countdown to the next check.
- Click **"📊 History"** to see interactive balance charts (Chart.js).
- Re-authenticate expired accounts with 1 click.

---

#### 💻 Option B: Run in Terminal / Console
```powershell
python main.py
```

---

## 🚀 How to Use

### 1. Authenticate Your Accounts
On first launch:
1. Select option `[2] Authenticate / Add New Account`.
2. Enter an Account ID (e.g., `account_1` or your nickname).
3. A browser window will open automatically navigating to Swagbucks.
4. Manually enter your credentials, complete CAPTCHA/2FA, and arrive at the dashboard.
5. Return to the terminal and press **Enter**.
6. Repeat for `account_2`, `account_3`, etc.

### 2. Start Live Monitoring
1. Choose option `[1] Start Simultaneous Monitoring`.
2. The dashboard will launch and poll all authenticated accounts concurrently.
3. Every 60 seconds (or your configured interval), it checks all balances and updates:
   - Current SB per account
   - Balance change (+/-)
   - Total SB across all accounts
   - Net change today
   - Next check countdown
4. Press `Ctrl + C` anytime to pause and return to the main menu.

### 3. Handling Expired Sessions
If a session expires:
- The dashboard marks that account as `SESSION EXPIRED` with an alert banner.
- All other accounts continue monitoring normally without crashing.
- Exit back to the menu and choose `[3] Re-authenticate an Existing Account` to refresh the session.

---

## 🔒 Security & Privacy

- **No Stored Passwords**: Your credentials are typed directly into the Chromium browser window; no passwords ever touch the code or files.
- **Git Protection**: `.gitignore` is pre-configured to ensure `browser_sessions/` and `data/` are never accidentally committed or exposed.
