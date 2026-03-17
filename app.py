import streamlit as st
import gspread
import pandas as pd
from oauth2client.service_account import ServiceAccountCredentials
import google.generativeai as genai
from datetime import datetime, timedelta
import dateutil.relativedelta
import base64
from PIL import Image
import io
import requests
import uuid
import warnings

# Ignore the Google AI warning for now
warnings.filterwarnings("ignore", category=FutureWarning)

# --- CONFIGURATION ---
st.set_page_config(page_title="Rental Manager (KSH)", layout="wide", initial_sidebar_state="expanded")

# --- CONSTANTS ---
NTFY_TOPIC = "home_rental_updates_8x2"

# --- SESSION STATE INIT ---
if "logged_in" not in st.session_state: st.session_state.logged_in = False
if "user_role" not in st.session_state: st.session_state.user_role = None
if "username" not in st.session_state: st.session_state.username = None
if "theme" not in st.session_state: st.session_state.theme = "Light"

# --- CLASSES ---
class DatabaseManager:
    def __init__(self):
        self.scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        self.client = None
        self.sheet = None

    def connect(self, creds_dict):
        try:
            creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, self.scope)
            self.client = gspread.authorize(creds)
            self.sheet = self.client.open_by_key(st.secrets["SPREADSHEET_ID"])
            return True
        except Exception as e:
            st.error(f"Database Connection Error: {e}")
            return False

    def get_df(self, tab_name):
        try:
            worksheet = self.sheet.worksheet(tab_name)
            data = worksheet.get_all_records()
            df = pd.DataFrame(data)
            return df
        except Exception as e:
            st.error(f"Error reading tab '{tab_name}': {e}")
            return pd.DataFrame()

    def append_row(self, tab_name, row_data):
        try:
            worksheet = self.sheet.worksheet(tab_name)
            worksheet.append_row(row_data)
        except Exception as e: st.error(f"Error saving data: {e}")

db = DatabaseManager()

class AIManager:
    def __init__(self):
        self.model = None
    
    def init_model(self, api_key):
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel('gemini-1.5-flash')

    def extract_receipt_data(self, image_bytes, mime_type):
        if not self.model: return None
        try:
            prompt = "Extract the total amount paid (look for KSH, KES, or M-Pesa) and the date. Return format: JSON {amount: float, date: string}"
            response = self.model.generate_content([
                {'mime_type': mime_type, 'data': image_bytes}, prompt])
            res_text = response.text.replace("```json", "").replace("```", "").strip()
            return eval(res_text) 
        except Exception as e:
            st.warning(f"AI could not read receipt clearly: {e}")
            return None

ai = AIManager()

class NotificationManager:
    @staticmethod
    def send_push(message):
        try:
            requests.post(f"https://ntfy.sh/{NTFY_TOPIC}", data=message.encode(encoding='utf-8'))
        except: pass

# --- HELPER FUNCTIONS ---
def apply_theme(theme):
    if theme == "Dark":
        st.markdown("""
        <style>
        .stApp { background-color: #0e1117; color: #fafafa; }
        .stSidebar { background-color: #262730; }
        </style>
        """, unsafe_allow_html=True)

def generate_id(): return str(uuid.uuid4())[:8]

# --- VIEW: LOGIN ---
def login_view():
    st.title("🏠 Rental Management System")
    st.write("Please login to continue")
    
    u = st.text_input("Username")
    p = st.text_input("Password", type="password")
    
    if st.button("Login"):
        if not st.session_state.get("db_connected"):
            st.error("Database not connected. Check secrets.")
            return

        users = db.get_df("Users")
        
        if users.empty:
            st.error("CRITICAL: 'Users' tab is empty or unreadable. Did you run the setup script and share the sheet with the Service Account email?")
            return

        # Simple validation
        match = users[(users['Username'] == u) & (users['Password'] == p)]
        
        if not match.empty:
            st.session_state.logged_in = True
            st.session_state.username = u
            st.session_state.user_role = match.iloc[0]['Role']
            st.session_state.theme = match.iloc[0].get('Theme', 'Light')
            st.rerun()
        else:
            st.error("Invalid credentials")

# --- VIEW: AGENT DASHBOARD ---
def agent_view():
    apply_theme(st.session_state.theme)
    st.sidebar.title(f"Agent: {st.session_state.username}")
    
    theme_choice = st.sidebar.radio("Theme", ["Light", "Dark"], index=0 if st.session_state.theme=="Light" else 1)
    if theme_choice != st.session_state.theme:
        st.session_state.theme = theme_choice
        st.rerun()

    menu = st.sidebar.radio("Menu", ["Dashboard", "Collect Rent", "Maintenance", "Submit Cash"])
    
    if menu == "Dashboard":
        st.header("Agent Dashboard")
        st.info("Welcome! Use the sidebar to collect rent or report issues.")
        
    elif menu == "Collect Rent":
        st.header("💵 Record Rent Payment")
        houses = db.get_df("Houses")
        tenants = db.get_df("Tenants")
        
        if houses.empty or tenants.empty:
            st.warning("Please add Houses and Tenants in the Google Sheet first.")
        else:
            col1, col2 = st.columns(2)
            with col1:
                sel_house = st.selectbox("Select House", houses['HouseID'].tolist())
                house_tenants = tenants[tenants['HouseID'] == sel_house]
                sel_tenant = st.selectbox("Tenant", house_tenants['Name'].tolist())
                amount = st.number_input("Amount Paid (KSH)", min_value=0)
                pay_method = st.selectbox("Method", ["Cash", "Bank Transfer", "M-Pesa"])
                
            with col2:
                st.info("Optional: Upload Receipt for AI Verification")
                receipt = st.file_uploader("Receipt Image", type=['png','jpg','jpeg'])
                
            if st.button("Submit Payment"):
                verified = False
                ai_match = False
                
                if receipt:
                    with st.spinner("AI Scanning Receipt..."):
                        if not ai.model: ai.init_model(st.secrets["GEMINI_API_KEY"])
                        img_bytes = receipt.read()
                        res = ai.extract_receipt_data(img_bytes, "image/jpeg")
                        
                        if res:
                            ai_amount = res.get('amount', 0)
                            st.write(f"AI Read Amount: KSH {ai_amount}")
                            
                            if abs(ai_amount - amount) < 10:
                                st.success("AI Verified: Amounts Match!")
                                verified = True
                                ai_match = True
                            else:
                                st.warning(f"Discrepancy! Agent entered {amount}, AI read {ai_amount}.")
                                st.info("Saving to Pending Verification for Admin approval.")
                                pending_row = [generate_id(), datetime.now().strftime("%Y-%m-%d"), sel_house, sel_tenant, amount, amount, ai_amount, "Link"]
                                db.append_row("Pending_Verification", pending_row)
                                NotificationManager.send_push(f"Verification Needed: {sel_house} rent mismatch")
                                st.stop()
                        else:
                            st.warning("AI could not read. Saving without verification.")
                
                trans_id = generate_id()
                row = [trans_id, datetime.now().strftime("%Y-%m-%d"), sel_house, sel_tenant, "Rent", amount, pay_method, "Link", verified, "FALSE", ""]
                db.append_row("Transactions", row)
                st.success("Payment Recorded Successfully!")
                
                if ai_match: NotificationManager.send_push(f"Rent Recorded: {sel_house} KSH {amount} (Verified)")
                else: NotificationManager.send_push(f"Rent Recorded: {sel_house} KSH {amount} (Manual)")

    elif menu == "Maintenance":
        st.header("🔧 Maintenance Request")
        houses = db.get_df("Houses")
        if houses.empty:
            st.warning("No houses found.")
        else:
            h = st.selectbox("House", houses['HouseID'].tolist())
            desc = st.text_area("Issue Description")
            deadline = st.date_input("Deadline", datetime.now() + timedelta(days=7))
            
            if st.button("Submit Request"):
                row = [generate_id(), h, desc, datetime.now().strftime("%Y-%m-%d"), deadline.strftime("%Y-%m-%d"), "Open", ""]
                db.append_row("Maintenance", row)
                NotificationManager.send_push(f"New Maintenance: {h} - {desc[:20]}...")
                st.success("Request submitted.")

    elif menu == "Submit Cash":
        st.header("📤 Submit Collected Cash to Admin")
        st.write("Upload proof of bank transfer/transaction")
        amount = st.number_input("Amount Sent (KSH)", min_value=0)
        proof = st.file_uploader("Receipt/Screenshot", type=['png','jpg','jpeg','pdf'])
        
        if st.button("Confirm Submission"):
            row = [datetime.now().strftime("%Y-%m-%d"), st.session_state.username, amount, "Link", "Pending Review"]
            db.append_row("Rent_Submissions", row)
            NotificationManager.send_push(f"Agent submitted KSH {amount} for review.")
            st.success("Submitted for Admin verification.")

# --- VIEW: ADMIN DASHBOARD ---
def admin_view():
    apply_theme("Light")
    st.sidebar.title(f"Admin")
    menu = st.sidebar.radio("Menu", ["Overview", "Financials", "Audit Log", "Pending Approvals", "Settings"])
    
    if menu == "Overview":
        st.header("📊 Real-time Overview")
        trans = db.get_df("Transactions")
        maint = db.get_df("Maintenance")
        
        if trans.empty:
            st.info("No data yet.")
        else:
            m1, m2, m3 = st.columns(3)
            with m1: st.metric("Total Transactions", len(trans))
            with m2: st.metric("Open Issues", len(maint[maint['Status']=='Open']))
            with m3: st.metric("Total Rent (This Month)", f"KSH {trans[trans['Type']=='Rent']['Amount'].sum()}")
            st.dataframe(trans.tail(10))
        
    elif menu == "Financials":
        st.header("💰 Financial Breakdown")
        trans = db.get_df("Transactions")
        houses = db.get_df("Houses")
        
        if trans.empty:
            st.info("No transactions yet.")
        else:
            trans['Date'] = pd.to_datetime(trans['Date'], errors='coerce')
            trans['Month'] = trans['Date'].dt.to_period('M').astype(str)
            
            st.subheader("Monthly Income")
            monthly = trans[trans['Type']=='Rent'].groupby('Month')['Amount'].sum().reset_index()
            st.bar_chart(monthly.set_index('Month'))
            
            st.subheader("⚠️ Cash Due (Overdue)")
            current_month = datetime.now().strftime("%Y-%m")
            active_tenants = db.get_df("Tenants")
            
            due_list = []
            for _, house in houses.iterrows():
                h_id = house['HouseID']
                house_trans = trans[(trans['HouseID']==h_id) & (trans['Type']=='Rent')]
                latest_pay = house_trans['Date'].max()
                if pd.isna(latest_pay) or latest_pay.strftime("%Y-%m") < current_month:
                    due_list.append({"House": h_id, "Status": "Missing/Overdue"})

            if due_list: st.dataframe(due_list)
            else: st.success("All rents up to date!")
            
    elif menu == "Audit Log":
        st.header("📜 Deleted Records Log")
        trans = db.get_df("Transactions")
        deleted = trans[trans['is_deleted'] == "TRUE"]
        if deleted.empty: st.info("No deleted records.")
        else: st.dataframe(deleted)
        
    elif menu == "Pending Approvals":
        st.header("⚠️ Pending Verification")
        pending = db.get_df("Pending_Verification")
        if pending.empty: st.info("No pending items.")
        else:
            for i, row in pending.iterrows():
                with st.expander(f"Issue: {row['HouseID']} - KSH {row['Amount']}"):
                    st.write(f"Agent Input: {row['AgentInput']}")
                    st.write(f"AI Read: {row['AIInput']}")
                    c1, c2 = st.columns(2)
                    if c1.button("Approve", key=f"app_{i}"):
                        new_row = [generate_id(), datetime.now().strftime("%Y-%m-%d"), row['HouseID'], row['TenantName'], "Rent", row['Amount'], "Manual", "Link", False, "FALSE", ""]
                        db.append_row("Transactions", new_row)
                        st.success("Approved & Moved to Transactions.")
                        st.rerun()
                    if c2.button("Reject", key=f"rej_{i}"):
                        st.warning("Rejected.")

    elif menu == "Settings":
        st.header("⚙️ App Settings")
        settings = db.get_df("App_Settings")
        st.dataframe(settings)
        
        st.subheader("Set Submission Deadline")
        d_day = st.number_input("Day of Month (1-28)", min_value=1, max_value=28)
        if st.button("Update Deadline"):
            st.success(f"Deadline updated to day {d_day}")

# --- MAIN CONTROLLER ---
def main():
    # Check connection status
    if "db_connected" not in st.session_state:
        try:
            # Attempt connection
            success = db.connect(st.secrets["GCP_CREDS"])
            if success:
                st.session_state.db_connected = True
            else:
                # If connect returns False (handled internal error)
                st.error("Connection Failed: Check if Service Account email is shared to the Sheet.")
                st.stop()
        except Exception as e:
            st.error(f"Critical Secret Error: {e}")
            st.stop()

    # If we are here, DB is connected.
    if not st.session_state.logged_in:
        login_view()
    else:
        if st.session_state.user_role == "Admin":
            admin_view()
        else:
            agent_view()
        
        if st.sidebar.button("Logout"):
            st.session_state.logged_in = False
            st.rerun()

if __name__ == "__main__":
    main()
