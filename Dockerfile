FROM python:3.12-slim-bookworm

# ============================================================
# SYSTEM DEPENDENCIES
# ============================================================

RUN apt-get update && apt-get install -y \
    curl \
    gnupg2 \
    unixodbc \
    unixodbc-dev \
    gcc \
    g++ \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# ============================================================
# MICROSOFT ODBC DRIVER 18 FOR SQL SERVER
# ============================================================

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

# ============================================================
# APPLICATION DIRECTORY
# ============================================================

WORKDIR /app

# ============================================================
# COPY APPLICATION
# ============================================================

COPY . .

# ============================================================
# PYTHON PACKAGE INSTALLATION
# ============================================================

RUN python -m pip install \
        --no-cache-dir \
        --retries 15 \
        --timeout 120 \
        --index-url https://pypi.org/simple \
        -r requirements.txt

# ============================================================
# VERIFY PYODBC + ODBC DRIVER
# ============================================================

RUN python -c "import pyodbc; print('============================================================'); print('PYODBC ODBC DRIVERS:'); print(pyodbc.drivers()); print('============================================================'); assert any('ODBC Driver 18 for SQL Server' in d for d in pyodbc.drivers()), 'ODBC Driver 18 for SQL Server NOT FOUND'"

# ============================================================
# PORT
# ============================================================

EXPOSE 10000

# ============================================================
# START APPLICATION
# ============================================================

CMD ["gunicorn", "config.wsgi:application", "--chdir", "BackEnd", "--bind", "0.0.0.0:10000", "--timeout", "600", "--workers", "1"]