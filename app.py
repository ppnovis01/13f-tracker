"""
13F Tracker — Dashboard de portfolios de hedge funds via SEC EDGAR

Como usar:
  streamlit run app.py

Para hospedar gratuitamente: https://streamlit.io/cloud
"""

import os
import sys

import pandas as pd
import streamlit as st

# Garante que o diretório src está no path
sys.path.insert(0, os.path.dirname(__file__))

from src.funds_config import FUNDS
from src.sec_fetcher import get_holdings, get_previous_quarter
from src.data_processor import (
    compute_overview_stats,
    compute_consensual_positions,
    compute_movers,
    get_portfolio_df,
)

# ── Config da página ──────────────────────────────────────────────────────────

st.set_page_config(
    page_title="13F Tracker",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── CSS personalizado (tema escuro) ───────────────────────────────────────────

st.markdown("""
<style>
/* Fundo geral */
.stApp { background-color: #0e0e0e; }

/* Sidebar */
[data-testid="stSidebar"] {
    background-color: #111111;
    border-right: 1px solid #222;
}

/* Cards de métricas */
[data-testid="stMetric"] {
    background-color: #1a1a1a;
    border: 1px solid #2a2a2a;
    border-radius: 6px;
    padding: 16px 20px;
}

/* Abas */
button[data-baseweb="tab"] { color: #888 !important; font-weight: 500; }
button[data-baseweb="tab"][aria-selected="true"] { color: #e05252 !important; }
[data-baseweb="tab-highlight"] { background-color: #e05252 !important; }

/* Tabelas */
[data-testid="stDataFrame"] { border: 1px solid #2a2a2a; border-radius: 6px; }

/* Botões */
.stButton > button {
    background-color: #1e1e1e;
    color: #e8e8e8;
    border: 1px solid #3a3a3a;
    border-radius: 4px;
}
.stButton > button:hover {
    background-color: #2a2a2a;
    border-color: #555;
}

/* Títulos da sidebar */
[data-testid="stSidebar"] h3 { font-size: 13px; color: #aaa; letter-spacing: 0.5px; }

/* Divisores */
hr { border-color: #2a2a2a; }

/* Subtítulos das seções */
.section-title {
    font-size: 14px;
    font-weight: 600;
    color: #e8e8e8;
    margin-bottom: 4px;
}
.section-subtitle {
    font-size: 12px;
    color: #777;
    margin-bottom: 16px;
}
</style>
""", unsafe_allow_html=True)


# ── Lista de trimestres disponíveis (estáticos + os que o usuário buscar) ─────

DEFAULT_QUARTERS = [
    "2025Q4", "2025Q3", "2025Q2", "2025Q1",
    "2024Q4", "2024Q3", "2024Q2", "2024Q1",
    "2023Q4", "2023Q3",
]


# ── Carregamento de dados (com cache em memória + JSON em disco) ──────────────

@st.cache_data(show_spinner=False)
def _load_fund(fund_name: str, cik: str, quarter: str, _cache_buster: int = 0) -> list:
    """Carrega as posições de uma gestora de um trimestre (usa cache JSON)."""
    return get_holdings(cik, fund_name, quarter, force_refresh=False)


def load_all_holdings(fund_names: list[str], quarter: str, cache_buster: int = 0) -> dict:
    """Carrega posições de todas as gestoras selecionadas."""
    result = {}
    for name in fund_names:
        cik = FUNDS[name]
        holdings = _load_fund(name, cik, quarter, _cache_buster=cache_buster)
        if holdings:
            result[name] = holdings
    return result


def refresh_from_sec(fund_names: list[str], quarter: str) -> int:
    """
    Força re-busca na SEC para todas as gestoras selecionadas.
    Retorna quantas gestoras tiveram dados encontrados.
    """
    found = 0
    progress = st.sidebar.progress(0.0, text="Buscando dados na SEC...")
    status_box = st.sidebar.empty()

    for i, name in enumerate(fund_names):
        status_box.caption(f"⏳ {name}")
        cik = FUNDS[name]
        holdings = get_holdings(cik, name, quarter, force_refresh=True)
        if holdings:
            found += 1
        progress.progress((i + 1) / len(fund_names), text=f"Buscando... {i+1}/{len(fund_names)}")

    progress.empty()
    status_box.empty()
    _load_fund.clear()  # Limpa cache em memória para refletir novos dados
    return found


# ── SIDEBAR ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("13F Tracker")
    st.caption("Dados de portfolio de hedge funds — SEC EDGAR")
    st.divider()

    # Trimestre
    st.markdown("**Trimestre**")
    selected_quarter = st.selectbox(
        "Trimestre", DEFAULT_QUARTERS, label_visibility="collapsed"
    )

    st.divider()

    # Seleção de gestoras
    st.markdown("**Gestoras**")
    col_all, col_none = st.columns(2)

    _EXCLUDED_FROM_DEFAULT = {
        "Alkeon Capital Management",
        "Bridgewater Associates",
        "Citadel Advisors",
        "Point72 Asset Management",
        "Renaissance Technologies",
        "Two Sigma Investments",
    }
    if "selected_funds" not in st.session_state:
        st.session_state.selected_funds = [f for f in FUNDS.keys() if f not in _EXCLUDED_FROM_DEFAULT]

    if col_all.button("Todas", use_container_width=True):
        st.session_state.selected_funds = list(FUNDS.keys())
        st.rerun()
    if col_none.button("Nenhuma", use_container_width=True):
        st.session_state.selected_funds = []
        st.rerun()

    selected_funds: list[str] = st.multiselect(
        "Gestoras",
        options=list(FUNDS.keys()),
        default=st.session_state.selected_funds,
        key="funds_multiselect",
        label_visibility="collapsed",
    )
    st.session_state.selected_funds = selected_funds

    st.divider()

    # Top-N para posições consensuais
    st.markdown("**Top N para sobreposição**")
    top_n = st.slider("Top N", min_value=1, max_value=20, value=5,
                      label_visibility="collapsed")

    st.divider()

    # Botão de atualização
    if st.button("🔄  Atualizar da SEC", use_container_width=True,
                 help="Busca os dados mais recentes diretamente do SEC EDGAR"):
        if not selected_funds:
            st.warning("Selecione ao menos uma gestora.")
        else:
            found = refresh_from_sec(selected_funds, selected_quarter)
            st.session_state["cache_buster"] = st.session_state.get("cache_buster", 0) + 1
            st.success(f"Concluído! {found}/{len(selected_funds)} gestoras com dados.")
            st.rerun()

    st.caption("ℹ️ Os dados ficam em cache local após a primeira busca.")


# ── MAIN ─────────────────────────────────────────────────────────────────────

# Carrega dados
cache_buster = st.session_state.get("cache_buster", 0)
all_holdings: dict = {}

if selected_funds:
    with st.spinner("Carregando dados..."):
        all_holdings = load_all_holdings(selected_funds, selected_quarter, cache_buster)

# Cabeçalho
st.title(selected_quarter)

n_loaded = len(all_holdings)
if n_loaded > 0:
    st.caption(f"{n_loaded} gestora(s) carregada(s)")
else:
    st.caption("Nenhum dado carregado")

if not all_holdings:
    st.info(
        "📋 **Sem dados para exibir.**\n\n"
        "Clique em **Atualizar da SEC** na barra lateral para buscar os dados do trimestre selecionado.\n\n"
        "A primeira busca pode levar alguns minutos. Após isso, os dados ficam em cache."
    )
    st.stop()

# Trimestre anterior
prev_quarter = get_previous_quarter(selected_quarter)
prev_holdings: dict = {}
if prev_quarter:
    prev_holdings = load_all_holdings(selected_funds, prev_quarter, cache_buster)

# ── Abas ─────────────────────────────────────────────────────────────────────

tab_overview, tab_movers, tab_portfolios = st.tabs(["📊 Overview", "📈 Movers", "📁 Portfolios"])


# ── ABA OVERVIEW ─────────────────────────────────────────────────────────────

with tab_overview:
    stats = compute_overview_stats(all_holdings)

    # Métricas
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("GESTORAS", f"{stats['n_funds']}")
    c2.metric("AÇÕES ÚNICAS", f"{stats['unique_stocks']:,}")
    c3.metric("TOTAL DE POSIÇÕES", f"{stats['total_positions']:,}")

    aum = stats["combined_aum_billions"]
    if aum >= 1_000:
        aum_str = f"${aum / 1_000:.2f}T"
    else:
        aum_str = f"${aum:.2f}B"
    c4.metric("AUM COMBINADO", aum_str)

    st.divider()

    # Posições mais consensuais
    st.markdown('<p class="section-title">Posições Mais Consensuais</p>', unsafe_allow_html=True)
    st.markdown(
        f'<p class="section-subtitle">Ações que aparecem no top-{top_n} de mais gestoras</p>',
        unsafe_allow_html=True,
    )

    consensual_df = compute_consensual_positions(
        all_holdings, top_n,
        prev_holdings=prev_holdings if prev_holdings else None,
    )

    if not consensual_df.empty:
        st.dataframe(
            consensual_df,
            use_container_width=True,
            hide_index=True,
            height=min(400, 40 + len(consensual_df) * 35),
        )

        # Export CSV
        csv_consensual = consensual_df.to_csv(index=False).encode("utf-8")
        st.download_button(
            "⬇️ Exportar CSV",
            data=csv_consensual,
            file_name=f"consensual_{selected_quarter}.csv",
            mime="text/csv",
        )
    else:
        st.info("Nenhuma posição encontrada.")


# ── ABA MOVERS ────────────────────────────────────────────────────────────────

with tab_movers:
    if not prev_holdings:
        st.info(
            f"Sem dados do trimestre anterior ({prev_quarter}) para comparação.\n\n"
            "Clique em **Atualizar da SEC** e, após carregar os dados deste trimestre, "
            "os dados do anterior serão buscados automaticamente."
        )
    else:
        new_df, closed_df = compute_movers(all_holdings, prev_holdings)

        col_new, col_closed = st.columns(2)

        with col_new:
            st.markdown(f"### 🟢 Novas Posições")
            st.caption(f"Ações que não existiam no {prev_quarter}")
            if not new_df.empty:
                st.dataframe(new_df, use_container_width=True, hide_index=True,
                             height=min(500, 40 + len(new_df) * 35))
                csv_new = new_df.to_csv(index=False).encode("utf-8")
                st.download_button("⬇️ Exportar CSV", csv_new,
                                   f"new_positions_{selected_quarter}.csv", "text/csv",
                                   key="dl_new")
            else:
                st.info("Nenhuma posição nova encontrada.")

        with col_closed:
            st.markdown(f"### 🔴 Posições Encerradas")
            st.caption(f"Ações que existiam no {prev_quarter} mas não neste trimestre")
            if not closed_df.empty:
                st.dataframe(closed_df, use_container_width=True, hide_index=True,
                             height=min(500, 40 + len(closed_df) * 35))
                csv_closed = closed_df.to_csv(index=False).encode("utf-8")
                st.download_button("⬇️ Exportar CSV", csv_closed,
                                   f"closed_positions_{selected_quarter}.csv", "text/csv",
                                   key="dl_closed")
            else:
                st.info("Nenhuma posição encerrada encontrada.")


# ── ABA PORTFOLIOS ────────────────────────────────────────────────────────────

with tab_portfolios:
    fund_list = list(all_holdings.keys())

    if not fund_list:
        st.info("Nenhum dado de portfolio disponível.")
    else:
        selected_fund = st.selectbox("Selecione a gestora", fund_list)

        if selected_fund:
            holdings = all_holdings[selected_fund]
            total_val = sum(h["value_usd"] for h in holdings) / 1e9

            col_a, col_b, col_c = st.columns(3)
            col_a.metric("POSIÇÕES", len(holdings))
            col_b.metric(
                "AUM REPORTADO",
                f"${total_val:.2f}B" if total_val >= 1 else f"${total_val * 1_000:.0f}M"
            )
            col_c.metric("TRIMESTRE", selected_quarter)

            st.divider()

            portfolio_df = get_portfolio_df(holdings)
            if not portfolio_df.empty:
                st.dataframe(
                    portfolio_df,
                    use_container_width=True,
                    hide_index=True,
                    height=min(600, 40 + len(portfolio_df) * 35),
                )

                csv_port = portfolio_df.to_csv(index=False).encode("utf-8")
                st.download_button(
                    "⬇️ Exportar CSV",
                    data=csv_port,
                    file_name=f"portfolio_{selected_fund.replace(' ', '_')}_{selected_quarter}.csv",
                    mime="text/csv",
                )
