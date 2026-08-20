# ============================================================
# STAGE 1 — BUILD ANGULAR FRONTEND
# ============================================================

FROM node:22-bookworm AS frontend-build

WORKDIR /app/FrontEnd

# Copy Angular package files first
COPY FrontEnd/package*.json ./

# Install Angular dependencies
RUN npm ci

# Copy Angular source
COPY FrontEnd/ .

# Build Angular production application
RUN npm run build


# ============================================================
# STAGE 2 — DJANGO BACKEND
# ============================================================

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
    build-essential \
    && rm -rf /var/lib/apt/lists/*


# ============================================================
# MICROSOFT ODBC DRIVER 18 FOR SQL SERVER
# ============================================================

RUN curl -fsSL -O https://packages.microsoft.com/config/debian/12/packages-microsoft-prod.deb \
    && dpkg -i packages-microsoft-prod.deb \
    && rm packages-microsoft-prod.deb \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y msodbcsql18 \
    && rm -rf /var/lib/apt/lists/*


# ============================================================
# APPLICATION DIRECTORY
# ============================================================

WORKDIR /app


# ============================================================
# COPY REQUIREMENTS FIRST
# ============================================================

COPY requirements.txt .


# ============================================================
# INSTALL PYTHON PACKAGES
# ============================================================

RUN pip install --upgrade pip && \
    pip install \
    --no-cache-dir \
    --retries 15 \
    --timeout 120 \
    -r requirements.txt


# ============================================================
# COPY APPLICATION
# ============================================================

COPY . .


# ============================================================
# COPY BUILT ANGULAR FRONTEND INTO DJANGO IMAGE
# ============================================================

COPY --from=frontend-build \
    /app/FrontEnd/dist/frontend/browser \
    /app/FrontEnd/dist/frontend/browser


# ============================================================
# VERIFY ANGULAR BUILD
# ============================================================

RUN test -f /app/FrontEnd/dist/frontend/browser/index.html && \
    echo "Angular frontend build found successfully"


# ============================================================
# VERIFY PYODBC + ODBC DRIVER
# ============================================================

RUN python -c "import pyodbc; print('ODBC DRIVERS:', pyodbc.drivers()); assert any('ODBC Driver 18 for SQL Server' in d for d in pyodbc.drivers())"


# ============================================================
# VERIFY SNOWFLAKE CONNECTOR
# ============================================================

RUN python -c "import snowflake.connector; print('Snowflake connector installed successfully')"


# ============================================================
# DJANGO STATIC FILES
# ============================================================

RUN python BackEnd/manage.py collectstatic --noinput


# ============================================================
# ENVIRONMENT
# ============================================================

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1


# ============================================================
# PORT
# ============================================================

EXPOSE 10000


# ============================================================
# START APPLICATION
# ============================================================

CMD ["gunicorn", "config.wsgi:application", "--chdir", "BackEnd", "--bind", "0.0.0.0:10000", "--timeout", "600", "--workers", "1"]