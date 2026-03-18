#!/bin/sh

# Create the full directory tree
mkdir -p backend/alembic/versions
mkdir -p backend/app/{core,models,schemas,routers,services}

# Move top-level config files
mv backend/requirements.txt backend/ 2>/dev/null || true # already there if you ran this before

# Alembic files
mv alembic.ini backend/
mv env.py backend/alembic/
mv script.py.mako backend/alembic/

# Migrations — note: delete 0002, it was merged into 0001 in the last session
mv 0001_initial_schema.py backend/alembic/versions/
rm -f 0002_wire_arrival_order_seq.py

# app/ root
mv __init__.py backend/app/
mv main.py backend/app/

# core/
touch backend/app/core/__init__.py
mv config.py backend/app/core/
mv database.py backend/app/core/
mv deps.py backend/app/core/
mv security.py backend/app/core/

# models/
touch backend/app/models/__init__.py
mv models.py backend/app/models/

# schemas/
touch backend/app/schemas/__init__.py
mv schemas.py backend/app/schemas/

# routers/
touch backend/app/routers/__init__.py
mv auth.py backend/app/routers/
mv staff.py backend/app/routers/
mv guests.py backend/app/routers/

# services/
touch backend/app/services/__init__.py
mv mazmo.py backend/app/services/
mv sync.py backend/app/services/

# Clean up the stray mnt/ directory (that's from my file outputs, not your project)
rm -rf mnt/
