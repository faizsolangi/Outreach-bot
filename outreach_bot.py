import smtplib
from email.mime.text import MIMEText
from langchain.chains import LLMChain
from langchain_core.prompts import PromptTemplate
from langchain_openai import ChatOpenAI
import streamlit as st
from crewai import Agent, Task, Crew
import os
from dotenv import load_dotenv
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import csv
import logging

# Configure logging
logging.basicConfig(filename='outreach_bot.log', level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

# Load environment variables
load_dotenv()

# Google Sheets setup
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
private_key = os.getenv("GOOGLE_PRIVATE_KEY")
logging.debug(f"Raw private_key: {private_key}")
if "\\n" in private_key:
    private_key = private_key.replace("\\n", "\n")
    logging.debug(f"Adjusted private_key: {private_key}")
creds_data = {
    "type": os.getenv("GOOGLE_TYPE", "service_account"),
    "project_id": os.getenv("GOOGLE_PROJECT_ID"),
    "private_key_id": os.getenv("GOOGLE_PRIVATE_KEY_ID"),
    "private_key": private_key,
    "client_email": os.getenv("GOOGLE_CLIENT_EMAIL"),
    "client_id": os.getenv("GOOGLE_CLIENT_ID"),
    "auth_uri": os.getenv("GOOGLE_AUTH_URI", "https://accounts.google.com/o/oauth2/auth"),
    "token_uri": os.getenv("GOOGLE_TOKEN_URI", "https://oauth2.googleapis.com/token"),
    "auth_provider_x509_cert_url": os.getenv("GOOGLE_AUTH_PROVIDER_X509_CERT_URL", "https://www.googleapis.com/oauth2/v1/certs"),
    "client_x509_cert_url": os.getenv("GOOGLE_CLIENT_X509_CERT_URL", "https://www.googleapis.com/robot/v1/metadata/x509/outreachbottracker%40coaching-leads-tracker.iam.gserviceaccount.com")
}
logging.debug(f"Credentials data: {creds_data}")
for key, value in creds_data.items():
    if not value:
        raise ValueError(f"Missing or empty environment variable: {key}")
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_data, scope)
client = gspread.authorize(creds)
spreadsheet_id = os.getenv("GOOGLE_SPREADSHEET_ID")
if not spreadsheet_id:
    raise ValueError("GOOGLE_SPREADSHEET_ID environment variable not set")
sheet = client.open_by_key(spreadsheet_id).sheet1

# Function to parse CSV file and save to Google Sheets
def parse_csv(file):
    logging.debug("Parsing CSV file")
    leads = []
    try:
        csv_file = file.read().decode('utf-8')
        # Check if file has headers; if not, assume default headers
        lines = csv_file.splitlines()
        if not lines or not any(lines[0].strip()):
            logging.warning("No data in CSV")
            return leads
        reader = csv.DictReader(lines, fieldnames=["name", "email", "industry", "status", "score"])
        for i, row in enumerate(reader):
            if i == 0 and all(k in row for k in ["name", "email", "industry", "status", "score"]):
                continue  # Skip header row if present
            lead = {
                "name": row.get("name", "Unknown"),
                "email": row.get("email", ""),
                "organization_industry": row.get("industry", "Unknown"),
                "status": row.get("status", "New"),
                "score": int(row.get("score", 0)) if row.get("score", "").isdigit() else 0
            }
            leads.append(lead)
            sheet.append_row([
                lead.get("name", "Unknown"),
                lead.get("email", ""),
                lead.get("organization_industry", "Unknown"),
                lead.get("status", "New"),
                lead.get("score", 0),
                ""
            ])
            logging.debug(f"Parsed lead: {lead}")
    except Exception as e:
        logging.error(f"Error parsing CSV: {str(e)}")
        st.write(f"Error parsing CSV: {str(e)}")
    return leads

# Function to process manual email input and save to Google Sheets
def process_emails(email_input):
    logging.debug("Processing manual email input")
    leads = []
    emails = [email.strip() for email in email_input.split(",") if email.strip()]
    for email in emails:
        lead = {
            "name": "Unknown",
            "email": email,
            "organization_industry": "Unknown",
            "status": "New",
            "score": 0
        }
        leads.append(lead)
        sheet.append_row([
            lead.get("name", "Unknown"),
            lead.get("email", ""),
            lead.get("organization_industry", "Unknown"),
            lead.get("status", "New"),
            lead.get("score", 0),
            ""
        ])
    logging.debug(f"Processed and saved {len(leads)} emails")
    return leads

# LangChain for generating emails
def generate_email(name, industry):
    llm = ChatOpenAI(model="gpt-4o-mini", openai_api_key=os.getenv("OPENAI_API_KEY"))
    template = """
    Subject: Free 15-Min Workflow Audit

    Hi {name},
    As a professional in the {industry} space, you could save hours weekly with AI automation.
    I’d love to offer you a free 15-min audit to optimize your processes.
    Best,
    [Your Name]
    solinnovate.io
    """
    prompt = PromptTemplate(template=template, input_variables=["name", "industry"])
    chain = LLMChain(llm=llm, prompt=prompt)
    return chain.run(name=name, industry=industry)

# Send email via Google Workspace SMTP
def send_email(to_email, email_content):
    msg = MIMEText(email_content)
    msg["Subject"] = "Free 15-Min Workflow Audit"
    msg["From"] = os.getenv("SMTP_USER", "outreach@solinnovate.io")
    msg["To"] = to_email
    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(os.getenv("SMTP_USER", "outreach@solinnovate.io"), os.getenv("SMTP_PASSWORD"))
        server.sendmail(os.getenv("SMTP_USER", "outreach@solinnovate.io"), to_email, msg.as_string())
    logging.debug(f"Email sent successfully to {to_email}")

# CrewAI for lead scoring
def score_leads(leads, industries=None):
    scorer = Agent(
        role="Lead Scorer",
        goal="Score leads based on provided data and selected industries",
        backstory="Expert in evaluating professionals across multiple industries",
        llm=ChatOpenAI(model="gpt-4o-mini", openai_api_key=os.getenv("OPENAI_API_KEY"))
    )
    industries = industries or ["Technology", "Healthcare"]
    task = Task(
        description=f"Score leads based on industry and title: +10 for titles containing industry-specific keywords ('{', '.join([ind.lower().split()[0] for ind in industries])}' for {', '.join(industries)}), +5 for 'Training' or 'Consultant' roles, +2 for leads in the selected industries {', '.join(industries)}",
        expected_output="A list of dictionaries containing 'name', 'score', and 'email' for each lead",
        agent=scorer
    )
    crew = Crew(agents=[scorer], tasks=[task])
    crew.kickoff()
    scored_leads = []
    for lead in leads:
        industry = lead.get("organization_industry", "").lower()
        title = lead.get("job_title", "").lower() if lead.get("job_title") else ""
        base_score = 2 if any(ind.lower() in industry for ind in industries) else 0
        title_score = 10 if any(keyword in title for keyword in [ind.lower().split()[0] for ind in industries]) else 5 if "training" in title or "consultant" in title else 0
        total_score = base_score + title_score
        scored_leads.append({"name": lead.get("name", "Unknown"), "score": total_score, "email": lead.get("email", "")})
    return scored_leads

# Streamlit dashboard
def run_dashboard():
    st.title("Multi-Industry Outreach Bot Demo")
    # Input options
    industries = st.multiselect("Select Industries", ["Technology", "Healthcare", "Coaching", "Education"], default=["Technology", "Healthcare"])
    uploaded_file = st.file_uploader("Upload CSV file (name, email, industry, status, score columns)", type="csv")
    email_input = st.text_input("Enter emails (comma-separated)", value="example1@email.com,example2@email.com")
    search_terms = st.text_input("Enter Search Terms (comma-separated)", value="Manager,Lead").split(",")
    search_terms = [term.strip() for term in search_terms if term.strip()]

    if "leads" not in st.session_state:
        if uploaded_file:
            st.session_state.leads = parse_csv(uploaded_file)
        elif email_input:
            st.session_state.leads = process_emails(email_input)
        else:
            st.session_state.leads = []
        st.write("Initial leads fetched:", len(st.session_state.leads))

    if st.button("Run Search"):
        try:
            if uploaded_file:
                st.session_state.leads = parse_csv(uploaded_file)
            elif email_input:
                st.session_state.leads = process_emails(email_input)
            else:
                st.session_state.leads = []
            st.write("Leads after search:", len(st.session_state.leads))
        except Exception as e:
            st.write(f"Error during search: {str(e)}")
        st.rerun()

    leads = st.session_state.leads
    st.write("### Leads Overview")
    if not leads:
        st.write("No leads found. Upload a CSV or enter emails to proceed.")
    for i, lead in enumerate(leads):
        name = lead.get("name", "Unknown")
        email = lead.get("email", "")
        industry = lead.get("organization_industry", "")
        score = next((s["score"] for s in score_leads([lead], industries) if s["name"] == name), 0)
        status = sheet.row_values(i + 2)[3] if i + 2 <= len(sheet.get_all_values()) else "New"
        st.write(f"Name: {name}, Email: {email}, Industry: {industry}, Status: {status}, Score: {score}")

    if st.button("Refresh Leads and Send Emails"):
        try:
            if uploaded_file:
                st.session_state.leads = parse_csv(uploaded_file)
            elif email_input:
                st.session_state.leads = process_emails(email_input)
            else:
                st.session_state.leads = []
            scored_leads = score_leads(st.session_state.leads, industries)
            for lead in scored_leads:
                if lead["email"]:
                    email_content = generate_email(lead["name"], lead.get("organization_industry", "Technology & Healthcare"))
                    send_email(lead["email"], email_content)
            st.write("Emails sent to valid addresses.")
        except Exception as e:
            st.write(f"Error during refresh: {str(e)}")
        st.rerun()

# Render web service entry point
import sys
import waitress

if __name__ == "__main__":
    try:
        if len(sys.argv) > 1 and sys.argv[1] == "render":
            waitress.serve(run_dashboard, host="0.0.0.0", port=8501)
        else:
            run_dashboard()
    except Exception as e:
        print(f"Error starting bot: {str(e)}")
        with open("outreach_bot.log", "a") as f:
            f.write(f"Error starting bot: {str(e)}\n")