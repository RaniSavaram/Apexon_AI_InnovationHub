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

4. Visit http://127.0.0.1:8000/ — Django will serve the built Angular app.

Whenever you change frontend code, re-run `npm run build` in frontend/,
then just restart/refresh — no need to redo Django setup.