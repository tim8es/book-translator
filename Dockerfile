FROM python:3.12-slim

WORKDIR /app

COPY requirements-actor.txt .
RUN pip install --no-cache-dir -r requirements-actor.txt

COPY . .

ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "actor_src"]
