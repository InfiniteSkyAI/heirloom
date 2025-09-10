FROM python:3.12-slim

# Set the working directory to the GitHub Actions directory.
WORKDIR /github/workspace

# Copy the requirements file and install dependencies.
COPY requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the Python script into the action's directory.
COPY github_issue_groomer.py github_issue_groomer.py

# Set the entrypoint to run the Python script from the correct location.
ENTRYPOINT ["python", "github_issue_groomer.py"]
