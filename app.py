from flask import Flask, request, render_template, jsonify
import os
import requests
import re
from requests.auth import HTTPBasicAuth

app = Flask(__name__)

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

# OpenAI endpoint (NOT Moonshot)
AI_API_URL = "https://api.openai.com/v1/chat/completions"
AI_API_KEY = os.environ.get("OPENAI_API_KEY")


# ---------------- FUNCTIONS ----------------

def generate_mom(meeting_text: str) -> str:
    if not AI_API_KEY:
        raise Exception("OPENAI_API_KEY is not set in environment variables")

    headers = {
        "Authorization": f"Bearer {AI_API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": "gpt-3.5-turbo",
        "messages": [
            {
                "role": "system",
                "content": "Extract project action items in this format:\n"
                           "1. **Issue:** description\n   - **Assigned to:** name"
            },
            {"role": "user", "content": meeting_text}
        ],
        "temperature": 0.3
    }

    response = requests.post(AI_API_URL, headers=headers, json=payload)

    if response.status_code != 200:
        raise Exception(f"AI API Error: {response.text}")

    data = response.json()
    return data["choices"][0]["message"]["content"]


def extract_relevant_points(mom_text: str):
    pattern = re.compile(
        r"\d+\.\s+\*\*Issue:\*\*\s+(.*?)\s*\n\s*-\s+\*\*Assigned to:\*\*\s+(\w+)",
        re.MULTILINE
    )
    return pattern.findall(mom_text)


def get_project_key_by_name(config_data, project_name):
    url = f"{config_data['jira_api_instance']}/rest/api/3/project/search"
    auth = HTTPBasicAuth(config_data['jira_email'], config_data['jira_api_token'])
    headers = {"Accept": "application/json"}

    response = requests.get(url, headers=headers, auth=auth)
    projects = response.json().get("values", [])

    for project in projects:
        if project["name"].lower() == project_name.lower():
            return project["key"]

    raise Exception("Project not found")


def get_account_id_by_name(config_data, assignee_name):
    url = f"{config_data['jira_api_instance']}/rest/api/3/user/search?query={assignee_name}"
    auth = HTTPBasicAuth(config_data['jira_email'], config_data['jira_api_token'])
    headers = {"Accept": "application/json"}

    response = requests.get(url, headers=headers, auth=auth)
    users = response.json()

    if not users:
        raise Exception(f"User not found: {assignee_name}")

    return users[0]["accountId"]


def create_jira_issue(config_data, issue_data):
    url = f"{config_data['jira_api_instance']}/rest/api/3/issue"
    auth = HTTPBasicAuth(config_data['jira_email'], config_data['jira_api_token'])
    headers = {"Accept": "application/json", "Content-Type": "application/json"}

    payload = {
        "fields": {
            "project": {"key": issue_data["project_key"]},
            "summary": issue_data["summary"],
            "description": issue_data["description"],
            "issuetype": {"name": "Task"},
            "assignee": {"accountId": issue_data["assignee_account_id"]}
        }
    }

    response = requests.post(url, json=payload, headers=headers, auth=auth)

    if response.status_code not in [200, 201]:
        raise Exception(f"Jira API Error: {response.text}")

    return response.json()


# ---------------- ROUTES ----------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/process", methods=["POST"])
def process():
    try:
        meeting_file = request.files["meeting_file"]
        file_path = os.path.join(app.config["UPLOAD_FOLDER"], meeting_file.filename)
        meeting_file.save(file_path)

        with open(file_path, "r", encoding="utf-8") as f:
            meeting_text = f.read()

        config_data = {
            "jira_email": request.form["jira_email"],
            "jira_api_token": request.form["jira_api_token"],
            "jira_api_instance": request.form["jira_api_instance"],
            "project_name": request.form["project_name"]
        }

        mom = generate_mom(meeting_text)
        points = extract_relevant_points(mom)
        project_key = get_project_key_by_name(config_data, config_data["project_name"])

        created_issues = []

        for description, assignee_name in points:
            account_id = get_account_id_by_name(config_data, assignee_name)

            issue_data = {
                "project_key": project_key,
                "summary": f"Action Item: {description}",
                "description": description,
                "assignee_account_id": account_id
            }

            issue = create_jira_issue(config_data, issue_data)
            created_issues.append(issue)

        return jsonify({
            "mom": mom,
            "created_issues": created_issues
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------- MAIN ----------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
