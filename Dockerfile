FROM python:3.14.0

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

CMD ["python", "runserver.py"]