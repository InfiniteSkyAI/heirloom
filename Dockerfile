# Use a lightweight Python base image
FROM python:3.11-slim

# Set the working directory
WORKDIR /app

# Copy the requirements file and install dependencies
COPY requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the main script
COPY github_issue_groomer.py github_issue_groomer.py

# Define the entrypoint for the container
ENTRYPOINT ["python", "github_issue_groomer.py"]
