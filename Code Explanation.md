# Frontend #
index.html
│
├── Dashboard()        ← dashboard page
├── Assets()           ← assets page  
├── WorkOrders()       ← work orders page
├── Inventory()        ← inventory page
├── PMSchedules()      ← PM schedules page
│
├── api()              ← calls backend API
├── useWS()            ← real-time sync
├── Badge()            ← status coloured badges
└── Modal()            ← popup windows

# Backend #
backend/
├── routers/work_orders.py   ← work order API endpoints
├── routers/assets.py        ← asset API endpoints
├── routers/inventory.py     ← inventory API endpoints
├── routers/pm_schedules.py  ← PM API endpoints
├── models.py                ← database tables
└── main.py                  ← dashboard KPI + app setup