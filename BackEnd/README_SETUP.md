# Backend setup

1. Build the Angular app first:
   cd ../frontend && npm run build

2. Create/activate a virtualenv, then install Django:
   python -m venv venv
   source venv/bin/activate      # Windows: venv\Scripts\activate
   pip install -r requirements.txt

3. Run migrations and start the server:
   python manage.py migrate
   python manage.py runserver

4. Visit https://apexon-ai-innovationhub-new.onrender.com/ — Render serves the Angular app and Django API from the same public URL.

Whenever you change frontend code, re-run `npm run build` in frontend/,
then just restart/refresh — no need to redo Django setup.