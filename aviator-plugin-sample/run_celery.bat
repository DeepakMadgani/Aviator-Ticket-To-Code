@echo off
set NEO4J_PASSWORD=aviator-dev
set POSTGRES_CONNECTION=postgresql://postgres:postgres@localhost:5433/postgres
set GOOGLE_APPLICATION_CREDENTIALS=C:\Users\dmadgani\Desktop\My_Aviator\aviator-plugin-sample\otl-cs-csai.json
set BROKER_URL=amqp://guest:guest@127.0.0.1:5672/
set PYTHONPATH=C:\Users\dmadgani\Desktop\My_Aviator\aviator-plugin-sample\src;C:\Users\dmadgani\Desktop\My_Aviator\aviator_adt\src
cd /d C:\Users\dmadgani\Desktop\My_Aviator
"C:\Users\dmadgani\Desktop\My_Aviator\aviator_adt\.venv\Scripts\python.exe" -m celery -A aviator.celery worker --loglevel=INFO --pool=solo
