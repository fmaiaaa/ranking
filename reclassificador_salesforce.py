import os
import re
import datetime
import pandas as pd
import streamlit as st
from simple_salesforce import Salesforce

# ==========================================
# CONFIGURAÇÕES DE INTERFACE E IDENTIDADE
# ==========================================
st.set_page_config(
    page_title="Reclassificador de Clientes | Salesforce",
    page_icon="⚡",
    layout="wide"
)

# Estilização profissional mantendo tipografia clássica e cores corporativas
st.markdown(
    """
    <style>
    html, body, [class*="css"] {
        font-family: 'Times New Roman', Times, serif;
    }
    .main-header {
        font-size: 26px;
        font-weight: bold;
        text-align: center;
        color: #03326c;
        margin-bottom: 5px;
    }
    .sub-header {
        font-size: 15px;
        text-align: center;
        color: #555555;
        margin-bottom: 25px;
    }
    .stButton>button {
        background-color: #04428f;
        color: white;
        font-family: 'Times New Roman', Times, serif;
        font-weight: bold;
        border-radius: 4px;
        border: none;
        padding: 0.5rem 1.5rem;
    }
    .stButton>button:hover {
        background-color: #03326c;
        color: white;
    }
    </style>
    """,
    unsafe_allow_html=True
)

# ==========================================
# CONEXÃO COM SALESFORCE
# ==========================================
@st.cache_resource(show_spinner=False)
def get_salesforce_connection():
    """
    Estabelece sessão com Salesforce via variáveis de ambiente ou st.secrets.
    """
    username = st.secrets.get("SALESFORCE_USERNAME", os.getenv("SALESFORCE_USERNAME", ""))
    password = st.secrets.get("SALESFORCE_PASSWORD", os.getenv("SALESFORCE_PASSWORD", ""))
    security_token = st.secrets.get("SALESFORCE_SECURITY_TOKEN", os.getenv("SALESFORCE_SECURITY_TOKEN", ""))
    domain = st.secrets.get("SALESFORCE_DOMAIN", os.getenv("SALESFORCE_DOMAIN", "login"))

    if not all([username, password, security_token]):
        st.error("Credenciais do Salesforce não configuradas em variáveis de ambiente ou st.secrets.")
        return None

    try:
        sf = Salesforce(
            username=username,
            password=password,
            security_token=security_token,
            domain=domain
        )
        return sf
    except Exception as e:
        st.error(f"Erro ao autenticar no Salesforce: {str(e)}")
        return None

# ==========================================
# FUNÇÕES DE HIGIENIZAÇÃO E BUSCA
# ==========================================
def clean_cpf(cpf_raw: str) -> str:
    """Higieniza e normaliza o documento CPF contendo apenas dígitos."""
    if not cpf_raw:
        return ""
    return re.sub(r"\D", "", str(cpf_raw)).zfill(11)

def query_salesforce_account(sf: Salesforce, cpf: str):
    """
    Localiza o registro de Conta e Oportunidades associadas pelo CPF informado.
    """
    clean_num = clean_cpf(cpf)
    if len(clean_num) != 11:
        return None, "CPF inválido. O formato deve conter 11 dígitos."

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
            return None, "Nenhum cliente cadastrado no Salesforce com o CPF fornecido."
        return records[0], None
    except Exception as e:
        return None, f"Erro ao consultar Salesforce: {str(e)}"

# ==========================================
# MOTOR DE CLASSIFICAÇÃO / RANKING
# ==========================================
def calculate_ranking(score: float, renda: float, idade: int) -> dict:
    """
    Aplica a lógica de scoring e ponderação de faixas para reclassificar o cliente.
    """
    # Normalização de inputs
    score = score or 0.0
    renda = renda or 0.0
    idade = idade or 0

    # Matriz de pesos
    pontos_score = min(score / 1000.0, 1.0) * 50.0

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

    if 25 <= idade <= 60:
        pontos_idade = 15.0
    elif idade > 60:
        pontos_idade = 10.0
    else:
        pontos_idade = 8.0

    total_pontos = pontos_score + pontos_renda + pontos_idade

    if total_pontos >= 80:
        faixa = "Rank A"
        risco = "Baixo Risco"
    elif total_pontos >= 60:
        faixa = "Rank B"
        risco = "Médio-Baixo Risco"
    elif total_pontos >= 40:
        faixa = "Rank C"
        risco = "Médio Risco"
    else:
        faixa = "Rank D"
        risco = "Alto Risco"

    return {
        "pontuacao": round(total_pontos, 2),
        "ranking": faixa,
        "risco": risco
    }

def update_salesforce_account(sf: Salesforce, account_id: str, new_ranking: str, new_risk: str) -> bool:
    """
    Atualiza os campos de reclassificação no registro de Account no Salesforce.
    """
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

# ==========================================
# INTERFACE PRINCIPAL
# ==========================================
def main():
    st.markdown('<div class="main-header">Reclassificação de Contas de Clientes</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-header">Consulta no Salesforce por CPF, recálculo de esteira e atualização direta em base</div>', unsafe_allow_html=True)

    sf = get_salesforce_connection()
    if not sf:
        st.stop()

    with st.container():
        col_input, col_btn = st.columns([3, 1])
        with col_input:
            cpf_digitado = st.text_input("Informe o CPF do Cliente (com ou sem formatação):", max_chars=14)
        with col_btn:
            st.write("")
            st.write("")
            buscar = st.button("Buscar Registro", use_container_width=True)

    if buscar and cpf_digitado:
        with st.spinner("Localizando registro no Salesforce..."):
            account_data, erro = query_salesforce_account(sf, cpf_digitado)

        if erro:
            st.warning(erro)
            st.session_state["account_found"] = None
        else:
            st.session_state["account_found"] = account_data
            st.success("Conta localizada com sucesso.")

    if st.session_state.get("account_found"):
        acc = st.session_state["account_found"]
        st.divider()

        st.markdown("### Dados Cadastrais no Salesforce")
        c1, c2, c3 = st.columns(3)
        c1.write(f"**Nome:** {acc.get('Name')}")
        c1.write(f"**ID Salesforce:** {acc.get('Id')}")
        c2.write(f"**CPF:** {acc.get('CPF__c')}")
        c2.write(f"**Idade Cadastrada:** {acc.get('Idade__c', 'N/D')}")
        c3.write(f"**Classificação Atual:** {acc.get('Ranking_Cliente__c', 'Não classificado')}")
        c3.write(f"**Risco Atual:** {acc.get('Classificacao_Risco__c', 'Não classificado')}")

        st.divider()
        st.markdown("### Parâmetros para Recálculo e Reclassificação")
        
        col_r1, col_r2, col_r3 = st.columns(3)
        with col_r1:
            renda_input = st.number_input(
                "Renda Familiar Atualizada (R$):",
                min_value=0.0,
                value=float(acc.get("Renda_Familiar__c") or 3500.0),
                step=500.0
            )
        with col_r2:
            score_input = st.number_input(
                "Score de Crédito:",
                min_value=0.0,
                max_value=1000.0,
                value=float(acc.get("Score_Credito__c") or 550.0),
                step=10.0
            )
        with col_r3:
            idade_input = st.number_input(
                "Idade:",
                min_value=18,
                max_value=100,
                value=int(acc.get("Idade__c") or 35),
                step=1
            )

        resultado = calculate_ranking(score_input, renda_input, idade_input)

        st.markdown("### Resultado da Nova Classificação")
        res_col1, res_col2, res_col3 = st.columns(3)
        res_col1.metric("Pontuação Calculada", f"{resultado['pontuacao']} pts")
        res_col2.metric("Novo Ranking", resultado["ranking"])
        res_col3.metric("Novo Risco", resultado["risco"])

        st.write("")
        if st.button("Confirmar e Salvar no Salesforce", use_container_width=True):
            with st.spinner("Atualizando registros no Salesforce..."):
                sucesso = update_salesforce_account(
                    sf=sf,
                    account_id=acc["Id"],
                    new_ranking=resultado["ranking"],
                    new_risk=resultado["risco"]
                )
                if sucesso:
                    st.success(f"Conta {acc['Name']} reclassificada com sucesso para {resultado['ranking']} ({resultado['risco']}).")
                    st.session_state["account_found"]["Ranking_Cliente__c"] = resultado["ranking"]
                    st.session_state["account_found"]["Classificacao_Risco__c"] = resultado["risco"]

if __name__ == "__main__":
    main()
