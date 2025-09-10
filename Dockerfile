FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt github_issue_groomer.py /app/

RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt \
 && rm -rf /root/.cache

ENTRYPOINT ["python", "-u", "/app/github_issue_groomer.py"]
