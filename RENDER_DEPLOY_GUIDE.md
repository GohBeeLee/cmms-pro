# Deploy CMMS to Render.com (FREE)

## What is Render?
Free cloud hosting. Your CMMS will be live at:
    https://cmms-pro.onrender.com  (or similar)

No credit card needed for free tier.

---

## STEP 1 — Install Git on your Windows PC
Download from: https://git-scm.com/download/win
Install with default settings.

Verify in cmd:
    git --version

---

## STEP 2 — Create a GitHub account
Go to: https://github.com
Sign up for free.

---

## STEP 3 — Create a GitHub repository
1. Click "+" → "New repository"
2. Name: cmms-pro
3. Set to Public
4. Click "Create repository"

---

## STEP 4 — Prepare your project folder

Your folder should look like this:
    cmms/
    ├── render.yaml          ← from this package
    ├── backend/
    │   ├── main.py          ← use main.py from this package
    │   ├── db.py
    │   ├── models.py
    │   ├── schemas.py
    │   ├── auth.py
    │   ├── websocket_manager.py
    │   ├── seed.py
    │   ├── requirements.txt
    │   └── routers/
    │       ├── __init__.py
    │       ├── assets.py
    │       ├── work_orders.py
    │       ├── inventory.py
    │       └── pm_schedules.py
    └── frontend/
        └── index.html

---

## STEP 5 — Push code to GitHub

Open cmd in your cmms folder:

    cd C:\Users\FACTORY USER\Desktop\cmms

    git init
    git add .
    git commit -m "Initial CMMS commit"
    git branch -M main
    git remote add origin https://github.com/YOURUSERNAME/cmms-pro.git
    git push -u origin main

Replace YOURUSERNAME with your actual GitHub username.

---

## STEP 6 — Deploy on Render.com

1. Go to https://render.com
2. Sign up (use GitHub login — easiest)
3. Click "New +" → "Web Service"
4. Click "Connect" next to your cmms-pro repository
5. Fill in settings:
   - Name: cmms-pro
   - Region: Singapore
   - Branch: main
   - Runtime: Python 3
   - Build Command:  pip install -r backend/requirements.txt
   - Start Command:  cd backend && uvicorn main:app --host 0.0.0.0 --port $PORT
6. Click "Create Web Service"

Render will build and deploy automatically (takes 2-3 minutes).

---

## STEP 7 — Access your live system

Render gives you a URL like:
    https://cmms-pro.onrender.com

Open it in any browser, anywhere in the world!

Login:
    Email:    admin@cmms.com
    Password: password123

---

## IMPORTANT NOTES

1. FREE TIER SLEEPS after 15 minutes of no activity.
   First visit after sleep takes ~30 seconds to wake up.
   This is normal for free tier.

2. DATABASE RESETS on each redeploy (SQLite is not persistent on free tier).
   To keep data permanently, upgrade to Render's $7/month plan
   which includes a persistent disk.

3. To UPDATE your system after code changes:
       git add .
       git commit -m "Update"
       git push
   Render auto-deploys on every push!

---

## UPGRADING (when you need persistent data)

On Render dashboard:
- Go to your service → Settings
- Add a Disk: mount path /data, size 1GB ($1/month)
- Update DATABASE_URL environment variable to:
  sqlite+aiosqlite:////data/cmms.db

---

## GETTING A CUSTOM DOMAIN (optional)

On Render dashboard:
- Settings → Custom Domains → Add your domain
- e.g. cmms.yourcompany.com
- Update DNS at your domain registrar (Cloudflare recommended)


## IF MAKE ANY CHANGES ON CODE##
cd "C:\Users\FACTORY USER\Desktop\cmms"
git add .
git commit -m "describe what you changed"
git push

- Render auto-detects and redeploys (2-3 mins)
- Refresh your website — changes are live