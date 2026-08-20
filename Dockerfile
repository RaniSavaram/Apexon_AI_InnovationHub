# ============================================================
# STAGE 1: BUILD FRONTEND
# ============================================================
FROM node:20-slim AS frontend-builder
WORKDIR /build

# Copy frontend packages and install
COPY FrontEnd/package*.json ./
RUN npm ci

# Copy frontend source and build
COPY FrontEnd/ ./
RUN npm run build

# ============================================================
# STAGE 2: BUILD BACKEND & RUN
# ============================================================
FROM python:3.12-slim-bookworm

# SYSTEM DEPENDENCIES
RUN apt-get update && apt-get install -y \
    curl \
    gnupg2 \
    unixodbc \
    unixodbc-dev \
    gcc \
    g++ \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# MICROSOFT ODBC DRIVER 18 FOR SQL SERVER
RUN curl -fsSL -O \
    https://packages.microsoft.com/config/debian/12/packages-microsoft-prod.deb \
    && dpkg -i packages-microsoft-prod.deb \
    && rm packages-microsoft-prod.deb \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y msodbcsql18 \
    && echo "============================================================" \
    && echo "INSTALLED ODBC DRIVERS" \
    && echo "============================================================" \
    && odbcinst -q -d \
    && echo "============================================================" \
    && echo "ODBC CONFIGURATION" \
    && odbcinst -j \
    && echo "============================================================" \
    && rm -rf /var/lib/apt/lists/*

# APPLICATION DIRECTORY
WORKDIR /app

# PYTHON PACKAGE INSTALLATION
COPY requirements.txt ./
RUN python -m pip install \
        --no-cache-dir \
        --retries 15 \
        --timeout 120 \
        --index-url https://pypi.org/simple \
        -r requirements.txt

# Copy Backend application
COPY BackEnd/ ./BackEnd/

# Copy built frontend from Stage 1 into the folder WhiteNoise expects
COPY --from=frontend-builder /build/dist/frontend/browser/ ./FrontEnd/dist/frontend/browser/

# VERIFY PYODBC + ODBC DRIVER
RUN python -c "import pyodbc; print('============================================================'); print('PYODBC ODBC DRIVERS:'); print(pyodbc.drivers()); print('============================================================'); assert any('ODBC Driver 18 for SQL Server' in d for d in pyodbc.drivers()), 'ODBC Driver 18 for SQL Server NOT FOUND'"

# PORT
EXPOSE 10000

# START APPLICATION
CMD ["gunicorn", "config.wsgi:application", "--chdir", "BackEnd", "--bind", "0.0.0.0:10000", "--timeout", "600", "--workers", "1"]