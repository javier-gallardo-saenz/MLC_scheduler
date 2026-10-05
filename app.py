"""Streamlit front-end:  streamlit run app.py

Two pages: the preference form for TAs, and the admin page that opens the form
and builds the schedule. Set `admin_password` in .streamlit/secrets.toml to
protect the admin page when the app is hosted.
"""
import streamlit as st

st.set_page_config(page_title="MLC Scheduler", layout="wide")
st.navigation([
    st.Page("views/form.py", title="Submit preferences", icon=":material/edit_note:", default=True),
    st.Page("views/admin.py", title="Build schedule", icon=":material/calendar_month:"),
]).run()
