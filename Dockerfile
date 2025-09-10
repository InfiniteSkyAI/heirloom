FROM python:3.12-slim

# Set the working directory to the GitHub Actions workspace.
# This is where the repository is checked out.
WORKDIR /github/workspace

# Copy the requirements file and install dependencies.
# The COPY command here is relative to the build context, which is the root of the repository.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy the Python script into the workspace.
COPY github_issue_groomer.py ./

# Set the entrypoint to run the Python script.
# This runs from the WORKDIR, so the file path is correct.
ENTRYPOINT ["python", "github_issue_groomer.py"]
