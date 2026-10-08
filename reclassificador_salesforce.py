import os
import re
import datetime
import pandas as pd
import streamlit as st
from simple_salesforce import Salesforce

# ==============================================================================
# CONFIGURAÇÃO DA PÁGINA E ESTILO VISUAL
# ==============================================================================
st.set_page_config(
    page_title="Reclassificador de Ranking | Direcional",
    page_icon="ranking-main/favicon.png" if os.path.exists("ranking-main/favicon.png") else (
        "favicon.png" if os.path.exists("favicon.png") else "⚡"
    ),
    layout="wide"
)

# Estilização estrita no padrão visual do app de consulta
st.markdown(
    """
    <style>
    html, body, [class*="css"] {
        font-family: 'Times New Roman', Times, serif !important;
    }
    .header-container {
        display: flex;
        align-items: center;
        justify-content: space-between;
        border-bottom: 2px solid #04428f;
        padding-bottom: 15px;
        margin-bottom: 25px;
    }
    .header-title {
        color: #04428f;
        font-size: 26px;
        font-weight: bold;
        margin: 0;
    }
    .header-subtitle {
        color: #555555;
        font-size: 14px;
        margin-top: 4px;
    }
    .card-box {
        background-color: #f8f9fa;
        border-radius: 6px;
        padding: 16px 20px;
        border-left: 5px solid #04428f;
        margin-bottom: 18px;
    }
    .card-box-alert {
        background-color: #fff5f5;
        border-radius: 6px;
        padding: 16px 20px;
        border-left: 5px solid #e30613;
        margin-bottom: 18px;
    }
    .badge-rank-a {
        background-color: #04428f;
        color: white;
        padding: 4px 10px;
        border-radius: 4px;
        font-weight: bold;
    }
    .badge-rank-b {
        background-color: #1a5fb4;
        color: white;
        padding: 4px 10px;
        border-radius: 4px;
        font-weight: bold;
    }
    .badge-rank-c {
        background-color: #e66100;
        color: white;
        padding: 4px 10px;
        border-radius: 4px;
        font-weight: bold;
    }
    .badge-rank-d {
        background-color: #e30613;
        color: white;
        padding: 4px 10px;
        border-radius: 4px;
        font-weight: bold;
    }
    .stButton>button {
        background-color: #04428f;
        color: #ffffff;
        font-family: 'Times New Roman', Times, serif;
        font-weight: bold;
        border-radius: 4px;
        border: none;
        padding: 0.5rem 1.2rem;
    }
    .stButton>button:hover {
        background-color: #03326c;
        color: #ffffff;
    }
    </style>
    """,
    unsafe_allow_html=True
)

# ==============================================================================
# IDENTIFICAÇÃO DE LOGOS LOCAIS
# ==============================================================================
def get_direcional_logo_path():
    possible_paths = [
        "ranking-main/502.57_LOGO DIRECIONAL_V2F-01.png",
        "502.57_LOGO DIRECIONAL_V2F-01.png",
        "ranking-main/502.57_LOGO D_COR_V3F.png",
        "502.57_LOGO D_COR_V3F.png"
    ]
    for path in possible_paths:
        if os.path.exists(path):
            return path
    return None

# ==============================================================================
# CONEXÃO COM SALESFORCE (salesforce_api)
# ==============================================================================
@st.cache_resource(show_spinner=False)
def get_salesforce_connection():
    username = st.secrets.get("SALESFORCE_USERNAME", os.getenv("SALESFORCE_USERNAME", ""))
    password = st.secrets.get("SALESFORCE_PASSWORD", os.getenv("SALESFORCE_PASSWORD", ""))
    security_token = st.secrets.get("SALESFORCE_SECURITY_TOKEN", os.getenv("SALESFORCE_SECURITY_TOKEN", ""))
    domain = st.secrets.get("SALESFORCE_DOMAIN", os.getenv("SALESFORCE_DOMAIN", "login"))

    if not all([username, password, security_token]):
        return None

    try:
        sf = Salesforce(
            username=username,
            password=password,
            security_token=security_token,
            domain=domain
        )
        return sf
    except Exception:
        return None

# ==============================================================================
# HIGIENIZAÇÃO E BUSCA NO SALESFORCE
# ==============================================================================
def clean_cpf(cpf_raw: str) -> str:
    if not cpf_raw:
        return ""
    return re.sub(r"\D", "", str(cpf_raw)).zfill(11)

def query_salesforce_account(sf: Salesforce, cpf: str):
    clean_num = clean_cpf(cpf)
    if len(clean_num) != 11:
        return None, "CPF inválido. Certifique-se de digitar os 11 dígitos."

    cpf_formatado = f"{clean_num[:3]}.{clean_num[3:6]}.{clean_num[6:9]}-{clean_num[9:]}"

    soql_query = f"""
        SELECT Id, Name, CPF__c, Renda_Familiar__c, Idade__c, Score_Credito__c, 
               Classificacao_Risco__c, Ranking_Cliente__c, Data_Ultima_Reclassificacao__c,
               (SELECT Id, StageName, Amount, CreatedDate FROM Opportunities ORDER BY CreatedDate DESC LIMIT 1)
        FROM Account
        WHERE CPF__c = '{clean_num}' OR CPF__c = '{cpf_formatado}'
        LIMIT 1
    """
    try:
        records = sf.query(soql_query).get("records", [])
        if not records:
            return None, "Cliente não encontrado na base do Salesforce."
        return records[0], None
    except Exception as e:
        return None, f"Falha na comunicação com o Salesforce: {str(e)}"

# ==============================================================================
# LÓGICA DE RECLASSIFICAÇÃO / RANKING
# ==============================================================================
def calculate_ranking(score: float, renda: float, idade: int) -> dict:
    score = float(score or 0.0)
    renda = float(renda or 0.0)
    idade = int(idade or 0)

    # Ponderação de score
    pontos_score = min(score / 1000.0, 1.0) * 50.0

    # Faixas de renda
    if renda >= 12000:
        pontos_renda = 35.0
    elif renda >= 7000:
        pontos_renda = 28.0
    elif renda >= 4000:
        pontos_renda = 20.0
    elif renda >= 2000:
        pontos_renda = 12.0
    else:
        pontos_renda = 5.0

    # Ponderação de faixa etária
    if 25 <= idade <= 60:
        pontos_idade = 15.0
    elif idade > 60:
        pontos_idade = 10.0
    else:
        pontos_idade = 8.0

    total_pontos = pontos_score + pontos_renda + pontos_idade

    if total_pontos >= 80:
        ranking = "Rank A"
        risco = "Baixo Risco"
        badge_cls = "badge-rank-a"
    elif total_pontos >= 60:
        ranking = "Rank B"
        risco = "Médio-Baixo Risco"
        badge_cls = "badge-rank-b"
    elif total_pontos >= 40:
        ranking = "Rank C"
        risco = "Médio Risco"
        badge_cls = "badge-rank-c"
    else:
        ranking = "Rank D"
        risco = "Alto Risco"
        badge_cls = "badge-rank-d"

    return {
        "pontuacao": round(total_pontos, 2),
        "ranking": ranking,
        "risco": risco,
        "badge_cls": badge_cls
    }

def update_salesforce_account(sf: Salesforce, account_id: str, new_ranking: str, new_risk: str) -> bool:
    hoje = datetime.datetime.now().strftime("%Y-%m-%d")
    payload = {
        "Ranking_Cliente__c": new_ranking,
        "Classificacao_Risco__c": new_risk,
        "Data_Ultima_Reclassificacao__c": hoje
    }
    try:
        sf.Account.update(account_id, payload)
        return True
    except Exception as e:
        st.error(f"Erro ao persistir reclassificação no Salesforce: {str(e)}")
        return False

# ==============================================================================
# APLICAÇÃO PRINCIPAL (LAYOUT CONSULTA)
# ==============================================================================
def main():
    # Cabeçalho com logo e título institucional alinhados
    logo_path = get_direcional_logo_path()
    
    col_header, col_logo = st.columns([4, 1])
    with col_header:
        st.markdown('<h1 class="header-title">Direcional Engenharia</h1>', unsafe_allow_html=True)
        st.markdown('<p class="header-subtitle">Módulo de Reclassificação e Atualização de Ranking de Clientes</p>', unsafe_allow_html=True)
    with col_logo:
        if logo_path:
            st.image(logo_path, use_container_width=True)

    st.write("")

    # Barra lateral de apoio com credenciais e status de conexão
    with st.sidebar:
        if logo_path:
            st.image(logo_path, width=170)
        st.markdown("### Ambiente Salesforce")
        sf = get_salesforce_connection()
        if sf:
            st.success("Conectado ao Salesforce")
        else:
            st.error("Desconectado do Salesforce")
            st.info("Configure as credenciais em `.streamlit/secrets.toml` ou variáveis de ambiente.")

        st.markdown("---")
        st.markdown("**Regras de Faixas de Risco:**")
        st.markdown("- **Rank A (>= 80 pts):** Baixo Risco")
        st.markdown("- **Rank B (60 a 79 pts):** Médio-Baixo")
        st.markdown("- **Rank C (40 a 59 pts):** Médio Risco")
        st.markdown("- **Rank D (< 40 pts):** Alto Risco")

    if not sf:
        st.warning("Aguardando configuração de conexão do Salesforce para habilitar a consulta.")
        st.stop()

    # Seção de Busca / Input de CPF idêntica ao layout da esteira de consulta
    st.markdown('<div class="card-box">', unsafe_allow_html=True)
    st.markdown("#### Consulta e Localização de Cadastro")
    
    col_input, col_action = st.columns([3, 1])
    with col_input:
        cpf_digitado = st.text_input("CPF do Cliente (apenas números ou formatado):", placeholder="Ex: 000.000.000-00", max_chars=14)
    with col_action:
        st.write("")
        st.write("")
        consultar = st.button("Consultar CPF", use_container_width=True)
    st.markdown('</div>', unsafe_allow_html=True)

    if consultar and cpf_digitado:
        with st.spinner("Buscando cadastro no Salesforce..."):
            account_data, err = query_salesforce_account(sf, cpf_digitado)
            if err:
                st.session_state["account_found"] = None
                st.markdown(f'<div class="card-box-alert"><strong>Atenção:</strong> {err}</div>', unsafe_allow_html=True)
            else:
                st.session_state["account_found"] = account_data
                st.success("Cadastro do cliente carregado com sucesso.")

    # Exibição de Dados e Painel de Reclassificação
    if st.session_state.get("account_found"):
        acc = st.session_state["account_found"]

        st.markdown("### 1. Dados Atuais da Conta")
        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown(f"**Cliente:** {acc.get('Name')}")
            st.markdown(f"**ID Salesforce:** `{acc.get('Id')}`")
        with c2:
            st.markdown(f"**CPF Cadastrado:** {acc.get('CPF__c') or '-'}")
            st.markdown(f"**Idade Base:** {acc.get('Idade__c') or '-'}")
        with c3:
            rank_atual = acc.get("Ranking_Cliente__c") or "Não Informado"
            risco_atual = acc.get("Classificacao_Risco__c") or "Não Informado"
            st.markdown(f"**Ranking Atual:** {rank_atual}")
            st.markdown(f"**Risco Atual:** {risco_atual}")

        st.write("")
        st.markdown("### 2. Parâmetros para Recálculo e Reclassificação")

        with st.container():
            col_renda, col_score, col_idade = st.columns(3)
            with col_renda:
                renda_val = st.number_input(
                    "Renda Familiar Comprovada (R$):",
                    min_value=0.0,
                    value=float(acc.get("Renda_Familiar__c") or 3500.0),
                    step=500.0
                )
            with col_score:
                score_val = st.number_input(
                    "Score de Crédito (0 a 1000):",
                    min_value=0.0,
                    max_value=1000.0,
                    value=float(acc.get("Score_Credito__c") or 550.0),
                    step=10.0
                )
            with col_idade:
                idade_val = st.number_input(
                    "Idade do Titular:",
                    min_value=18,
                    max_value=100,
                    value=int(acc.get("Idade__c") or 30),
                    step=1
                )

        resultado = calculate_ranking(score_val, renda_val, idade_val)

        st.write("")
        st.markdown("### 3. Simulação da Nova Classificação")
        
        m1, m2, m3 = st.columns(3)
        m1.metric("Pontuação Total", f"{resultado['pontuacao']} pts")
        m2.metric("Novo Ranking", resultado["ranking"])
        m3.metric("Classificação de Risco", resultado["risco"])

        st.write("")
        col_btn_save, col_espaco = st.columns([2, 3])
        with col_btn_save:
            salvar = st.button("Confirmar Reclassificação no Salesforce", use_container_width=True)

        if salvar:
            with st.spinner("Atualizando campos no Salesforce..."):
                ok = update_salesforce_account(
                    sf=sf,
                    account_id=acc["Id"],
                    new_ranking=resultado["ranking"],
                    new_risk=resultado["risco"]
                )
                if ok:
                    st.success(f"Conta '{acc['Name']}' reclassificada com sucesso para {resultado['ranking']} ({resultado['risco']}).")
                    st.session_state["account_found"]["Ranking_Cliente__c"] = resultado["ranking"]
                    st.session_state["account_found"]["Classificacao_Risco__c"] = resultado["risco"]

if __name__ == "__main__":
    main()
