FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=6000
ENV PYTHONUNBUFFERED=1

EXPOSE 6000

CMD ["python", "app.py"]
