# -*- coding: utf-8 -*-
"""
Consulta de Ranking do Cliente - Streamlit
Busca por ID da Oportunidade ou CPF (Salesforce).
Design de referência: Direcional (Simulador Imobiliário).
"""

import os
import re

import streamlit as st

from salesforce_api import conectar_salesforce


COR_AZUL_ESC = "#002c5d"
COR_VERMELHO = "#e30613"
COR_FUNDO = "#fcfdfe"
COR_BORDA = "#eef2f6"
COR_TEXTO_MUTED = "#64748b"
COR_INPUT_BG = "#f0f2f6"


def aplicar_estilo() -> None:
    st.markdown(
        f"""
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Montserrat:wght@400;500;600;700;800;900&family=Inter:wght@300;400;500;600;700&display=swap');

        html, body, [data-testid="stAppViewContainer"] {{
            font-family: 'Inter', sans-serif;
            color: {COR_AZUL_ESC};
            background-color: {COR_FUNDO};
        }}

        h1, h2, h3, h4 {{
            font-family: 'Montserrat', sans-serif !important;
            color: {COR_AZUL_ESC} !important;
            font-weight: 800;
            text-align: center;
        }}

        .block-container {{ max-width: 1200px !important; padding: 2rem !important; }}

        div[data-baseweb="input"] {{
            border-radius: 8px !important;
            border: 1px solid #e2e8f0 !important;
            background-color: {COR_INPUT_BG} !important;
        }}

        .row-widget.stButton,
        div[data-testid="column"]:has(.stButton),
        div[data-testid="stVerticalBlock"] > div:has(.stButton),
        .stButton {{
            width: 100% !important;
            max-width: 100% !important;
        }}

        .stButton {{
            display: block !important;
        }}

        .stButton button {{
            font-family: 'Inter', sans-serif;
            border-radius: 8px !important;
            padding: 0 20px !important;
            box-sizing: border-box !important;
            width: 100% !important;
            max-width: 100% !important;
            height: 38px !important;
            min-height: 38px !important;
            font-weight: 700 !important;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }}

        .stButton button[kind="primary"] {{
            background: {COR_VERMELHO} !important;
            color: #ffffff !important;
            border: none !important;
        }}

        .stButton button[kind="primary"]:hover {{
            background: #c40510 !important;
        }}

        .header-container {{
            text-align: center;
            padding: 40px 0;
            background: #ffffff;
            margin-bottom: 40px;
            border-radius: 0 0 24px 24px;
            border-bottom: 1px solid {COR_BORDA};
            box-shadow: 0 10px 25px -15px rgba(0,44,93,0.15);
        }}

        .header-title {{
            font-family: 'Montserrat', sans-serif;
            color: {COR_AZUL_ESC};
            font-size: 2rem;
            font-weight: 900;
            margin: 0;
            text-transform: uppercase;
            letter-spacing: 0.15em;
        }}

        .header-subtitle {{
            color: {COR_AZUL_ESC};
            font-size: 0.95rem;
            font-weight: 600;
            margin-top: 10px;
            opacity: 0.85;
        }}

        .card {{
            background: #ffffff;
            padding: 24px;
            border-radius: 16px;
            border: 1px solid {COR_BORDA};
            margin-bottom: 24px;
        }}

        .footer {{
            text-align: center;
            padding: 40px 0;
            color: {COR_AZUL_ESC};
            font-size: 0.8rem;
            opacity: 0.7;
        }}

        .hover-card {{
            background-color: #ffffff;
            border-radius: 12px;
            padding: 18px 16px;
            border: 1px solid #eef2f6;
            box-shadow: 0 4px 6px rgba(0, 0, 0, 0.05);
            transition: transform 0.3s ease, box-shadow 0.3s ease, border-color 0.3s ease;
            height: 130px;
            display: flex;
            flex-direction: column;
            justify-content: center;
            align-items: center;
        }}
        .hover-card:hover {{
            transform: translateY(-4px);
            box-shadow: 0 10px 20px rgba(0, 44, 93, 0.15);
            border-color: {COR_VERMELHO};
        }}
        .hover-card-label {{
            font-size: 0.75rem;
            text-transform: uppercase;
            letter-spacing: 0.12em;
            color: {COR_TEXTO_MUTED};
            margin-bottom: 4px;
            font-weight: 700;
        }}
        .hover-card-value {{
            font-size: 1.05rem;
            font-weight: 800;
            color: {COR_VERMELHO};
            word-break: break-word;
            text-align: center;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def normalizar_id_oportunidade(valor: str) -> str:
    """
    Normaliza o ID da Oportunidade vindo da base (Looker/planilha):
    - remove espaços, hífens, pontos e underscores
    - converte para maiúsculo
    """
    if not valor:
        return ""
    s = str(valor).strip().upper()
    s = re.sub(r"[\\s\\-\\._]+", "", s)
    return s


def normalizar_cpf(valor: str) -> str:
    """
    Normaliza CPF digitado pelo usuário removendo tudo que não é número.
    """
    if not valor:
        return ""
    return re.sub(r"\D+", "", str(valor))


def consultar_ranking_por_id(sf, id_oportunidade_bruto: str):
    """
    Consulta no Salesforce a oportunidade pelo campo customizado IDOportunidade__c
    e retorna os dados relevantes para exibir o ranking do cliente.
    """
    if not id_oportunidade_bruto or not id_oportunidade_bruto.strip():
        return None, "Informe um ID da Oportunidade."

    # Mantém o valor original (como vem da base) para comparação direta
    id_original = id_oportunidade_bruto.strip()
    id_normalizado = normalizar_id_oportunidade(id_original)

    # Busca principal: igualdade exata no campo IDOportunidade__c
    # (assumindo que o valor armazenado no SF é igual ao ID que você digita).
    soql = f"""
        SELECT
            Id,
            Name,
            IDOportunidade__c,
            AccountId,
            Account.Name,
            Account.CPF__c,
            Account.Ranking__c,
            Account.Ranking_Score__c,
            Ranking__c,
            Ranking_Score__c
        FROM Opportunity
        WHERE IDOportunidade__c = '{id_original}'
        LIMIT 10
    """

    try:
        res = sf.query(soql)
        registros = res.get("records", [])

        # Se nada encontrado com igualdade exata, tenta por "contains" usando SOQL simples,
        # para cobrir casos em que o campo tenha formatação diferente (hífens, pontos etc.).
        if not registros:
            soql_fallback = f"""
                SELECT
                    Id,
                    Name,
                    IDOportunidade__c,
                    AccountId,
                    Account.Name,
                    Account.CPF__c,
                    Account.Ranking__c,
                    Account.Ranking_Score__c,
                    Ranking__c,
                    Ranking_Score__c
                FROM Opportunity
                WHERE IDOportunidade__c LIKE '%{id_normalizado}%'
                LIMIT 10
            """
            res_fb = sf.query(soql_fallback)
            registros = res_fb.get("records", [])

        if not registros:
            return None, f"Nenhuma oportunidade encontrada para o ID informado: {id_original!r}."

        # Se houver mais de uma, pega a primeira (pode ser refinado depois se necessário)
        opp = registros[0]
        return opp, None

    except Exception as e:
        return None, f"Erro ao consultar Salesforce: {e}"


def consultar_por_cpf(sf, cpf_bruto: str):
    """
    Consulta oportunidades a partir do CPF da conta (Account.CPF__c)
    e retorna a oportunidade mais recente encontrada + dados da conta.
    """
    # Normaliza para dígitos e mascara de volta no padrão XXX.XXX.XXX-XX,
    # pois Account.CPF__c está armazenado com máscara (ex.: 076.086.171-44).
    cpf_digitos = normalizar_cpf(cpf_bruto)
    if not cpf_digitos or len(cpf_digitos) != 11:
        return None, "Informe um CPF válido (11 dígitos)."

    cpf_mascarado = f"{cpf_digitos[0:3]}.{cpf_digitos[3:6]}.{cpf_digitos[6:9]}-{cpf_digitos[9:11]}"

    soql = f"""
        SELECT
            Id,
            Name,
            IDOportunidade__c,
            AccountId,
            Account.Name,
            Account.CPF__c,
            Account.Ranking__c,
            Account.Ranking_Score__c,
            Ranking__c,
            Ranking_Score__c
        FROM Opportunity
        WHERE Account.CPF__c = '{cpf_mascarado}'
        ORDER BY CreatedDate DESC
        LIMIT 10
    """

    try:
        res = sf.query(soql)
        registros = res.get("records", [])
        if not registros:
            return None, f"Nenhuma oportunidade encontrada para o CPF informado: {cpf_bruto!r} (normalizado/máscara: {cpf_mascarado})."
        opp = registros[0]
        return opp, None
    except Exception as e:
        return None, f"Erro ao consultar Salesforce por CPF: {e}"


def main():
    st.set_page_config(
        page_title="Ranking do Cliente - Salesforce",
        page_icon="favicon.png",
        layout="centered",
    )
    aplicar_estilo()

    st.markdown(
        '<div class="header-container">'
        '<div class="header-title">Ranking Cliente</div>'
        '<div class="header-subtitle">Consulta por Oportunidade ou CPF (Salesforce)</div>'
        '</div>',
        unsafe_allow_html=True,
    )

    # Estado compartilhado
    if "sf" not in st.session_state:
        st.session_state.sf = None
    if "ultimo_resultado" not in st.session_state:
        st.session_state.ultimo_resultado = None

    # Texto explicativo com destaque para IDOportunidade__c em vermelho Direcional
    st.markdown(
        f"""
<p style="text-align:center; margin-bottom:0.75rem; font-size:0.95rem; color:{COR_AZUL_ESC};">
Informe abaixo o <b>CPF do cliente</b> (com ou sem máscara) ou o <b>ID da Oportunidade</b>
(<span style="color:{COR_VERMELHO}; font-weight:800;">IDOportunidade__c</span>) para consultar
o ranking do cliente associado no Salesforce.
</p>
        """,
        unsafe_allow_html=True,
    )

    # Campo de busca e botão
    entrada_principal = st.text_input("CPF ou ID da Oportunidade", value="")

    if st.button("Consultar Ranking", type="primary", use_container_width=True, key="btn_consultar"):
        texto = entrada_principal.strip()
        if not texto:
            st.warning("Por favor, informe um CPF ou ID da Oportunidade.")
        else:
            # Decide: se entrada tem 11 dígitos é tratada como CPF, caso contrário como ID de Oportunidade
            cpf_digitos = normalizar_cpf(texto)
            eh_cpf = len(cpf_digitos) == 11

            # Conecta ao Salesforce (ou reutiliza conexão da sessão)
            if st.session_state.sf is None:
                if "salesforce" in st.secrets:
                    sec = st.secrets["salesforce"]
                    os.environ["SALESFORCE_USER"] = sec.get("USER", "")
                    os.environ["SALESFORCE_PASSWORD"] = sec.get("PASSWORD", "")
                    os.environ["SALESFORCE_TOKEN"] = sec.get("TOKEN", "")
                with st.spinner("Conectando ao Salesforce..."):
                    sf = conectar_salesforce()
                if not sf:
                    st.error(
                        "Não foi possível conectar ao Salesforce. "
                        "Verifique as variáveis de ambiente SALESFORCE_USER, SALESFORCE_PASSWORD e SALESFORCE_TOKEN"
                    )
                else:
                    st.session_state.sf = sf

            if st.session_state.sf is not None:
                with st.spinner("Consultando dados no Salesforce..."):
                    if eh_cpf:
                        opp, erro = consultar_por_cpf(st.session_state.sf, texto)
                    else:
                        opp, erro = consultar_ranking_por_id(st.session_state.sf, texto)

                if erro:
                    st.markdown(
                        f"""
<div style="margin-top:16px; padding:12px 16px; border-radius:8px;
            border:1px solid {COR_VERMELHO}; background:#fff5f5;
            color:{COR_VERMELHO}; font-weight:600; text-align:center;">
{erro}
</div>
                        """,
                        unsafe_allow_html=True,
                    )
                    st.session_state.ultimo_resultado = None
                else:
                    conta = opp.get("Account") or {}
                    dados_prontos = {
                        "nome_conta": conta.get("Name"),
                        "cpf": conta.get("CPF__c"),
                        "ranking_conta": conta.get("Ranking__c"),
                        "ranking_conta_score": conta.get("Ranking_Score__c"),
                    }
                    st.session_state.ultimo_resultado = dados_prontos

    # Exibição dos dados logo abaixo do botão, dentro do mesmo card
    dados = st.session_state.ultimo_resultado
    if dados:
        col3 = st.columns(1)

        with col3:
            st.markdown(
                f"""
<div class="hover-card">
  <div class="hover-card-label">Ranking</div>
  <div class="hover-card-value">{dados.get('ranking_conta') or '—'}</div>
</div>
                """,
                unsafe_allow_html=True,
            )

    st.markdown('<div class="footer">Direcional Engenharia | Consulta de Ranking</div>', unsafe_allow_html=True)


if __name__ == "__main__":
    main()

