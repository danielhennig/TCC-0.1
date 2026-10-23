"""Ponto de entrada do painel: `python tasks.py dashboard` (http://localhost:8501)."""

from __future__ import annotations

import streamlit as st

from dashboard import common
from dashboard.pages import alerts, data, databases, overview, users

st.set_page_config(page_title="SACLA — painel", page_icon=":material/shield:", layout="wide")

pages = st.navigation(
    [
        st.Page(
            overview.render,
            title="Visão geral",
            icon=":material/dashboard:",
            default=True,  # a página padrão fica na raiz (/); não aceita url_path
        ),
        st.Page(
            databases.render,
            title="Bancos supervisionados",
            icon=":material/database:",
            url_path="bancos",
        ),
        st.Page(
            alerts.render,
            title="Alertas",
            icon=":material/notification_important:",
            url_path="alertas",
        ),
        st.Page(
            users.render, title="Usuários", icon=":material/person_search:", url_path="usuarios"
        ),
        st.Page(data.render, title="Dados", icon=":material/table_view:", url_path="dados"),
    ]
)
with st.sidebar:
    st.markdown("**SACLA** — acessos atípicos no PostgreSQL")
common.new_alerts_notice()
pages.run()
