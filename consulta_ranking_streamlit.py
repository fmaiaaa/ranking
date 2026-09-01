# -*- coding: utf-8 -*-
"""
Consulta de Ranking do Cliente — Direcional
Consulta por CPF no Salesforce.

Fluxo: SOQL na Account; se não houver conta, cria DIRESIMULATOR temporária,
consulta Risk3, exibe o ranking e remove a conta ao final.
"""

import os

import streamlit as st
from tqdm import tqdm

from ranking_cliente import consultar_ranking, normalizar_cpf, regional_comercial_padrao
from salesforce_api import conectar_salesforce


COR_AZUL_ESC = "#002c5d"
COR_VERMELHO = "#e30613"
COR_FUNDO = "#fcfdfe"
COR_BORDA = "#eef2f6"
COR_TEXTO_MUTED = "#64748b"
COR_INPUT_BG = "#f0f2f6"
TIMEOUT_CONSULTA_SEG = 90.0
INTERVALO_POLL_SEG = 5.0


class TqdmDirecional:
    """Barra tqdm com gradiente azul → vermelho Direcional para o Streamlit."""

    def __init__(self, total: int, desc: str = "Consultando ranking..."):
        self.total = max(1, total)
        self.desc = desc
        self.n = 0
        self._slot = st.empty()
        self._tqdm = tqdm(total=self.total, desc=desc, leave=False, dynamic_ncols=True)
        self._render()

    def update(self, n: int = 1) -> None:
        self.n = min(self.n + n, self.total)
        self._tqdm.update(n)
        self._render()

    def finish(self) -> None:
        restante = self.total - self.n
        if restante > 0:
            self.update(restante)

    def close(self) -> None:
        self._tqdm.close()
        self._slot.empty()

    def _render(self) -> None:
        pct = (self.n / self.total) * 100
        self._slot.markdown(
            f"""
<div class="direcional-tqdm">
  <div class="direcional-tqdm-header">
    <span class="direcional-tqdm-desc">{self.desc}</span>
    <span class="direcional-tqdm-pct">{pct:.0f}%</span>
  </div>
  <div class="direcional-tqdm-track">
    <div class="direcional-tqdm-fill" style="width:{pct:.1f}%;"></div>
  </div>
</div>
            """,
            unsafe_allow_html=True,
        )


def total_passos_consulta(forcar_atualizacao: bool) -> int:
    poll_passos = int(TIMEOUT_CONSULTA_SEG / INTERVALO_POLL_SEG)
    overhead = 4 if forcar_atualizacao else 3
    return poll_passos + overhead


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
            width: 100%;
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

        .direcional-tqdm {{
            margin: 1rem 0 1.25rem;
            padding: 0.25rem 0;
        }}

        .direcional-tqdm-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 0.45rem;
            font-size: 0.85rem;
            font-weight: 600;
            color: {COR_AZUL_ESC};
        }}

        .direcional-tqdm-pct {{
            color: {COR_VERMELHO};
            font-weight: 800;
        }}

        .direcional-tqdm-track {{
            width: 100%;
            height: 12px;
            border-radius: 999px;
            background: #e8edf3;
            overflow: hidden;
            border: 1px solid #dbe4ee;
        }}

        .direcional-tqdm-fill {{
            height: 100%;
            border-radius: 999px;
            background: linear-gradient(90deg, {COR_AZUL_ESC} 0%, {COR_VERMELHO} 100%);
            transition: width 0.35s ease;
            box-shadow: 0 1px 4px rgba(227, 6, 19, 0.25);
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def main():
    st.set_page_config(
        page_title="Consulta de Ranking — Direcional",
        page_icon="favicon.png",
        layout="centered",
    )
    aplicar_estilo()

    st.markdown(
        '<div class="header-container">'
        '<div class="header-title">Consulta de Ranking</div>'
        '<div class="header-subtitle">Informe o CPF — consulta SOQL; sem conta, cria temporária e consulta Risk3</div>'
        '</div>',
        unsafe_allow_html=True,
    )

    if "sf" not in st.session_state:
        st.session_state.sf = None
    if "ultimo_resultado" not in st.session_state:
        st.session_state.ultimo_resultado = None

    st.markdown(
        f"""
<p style="text-align:center; margin-bottom:0.75rem; font-size:0.95rem; color:{COR_AZUL_ESC};">
Digite o <b>CPF do cliente</b> (com ou sem formatação) para consultar o ranking.
</p>
        """,
        unsafe_allow_html=True,
    )

    cpf_entrada = st.text_input("CPF do cliente", value="", placeholder="Ex.: 000.000.000-00")
    forcar_atualizacao = st.checkbox(
        "Atualizar ranking (consulta Risk3)",
        value=False,
        help=(
            "Com conta existente, dispara IntegracaoRisk3 e aguarda o ranking. "
            "Sem conta, o app cria uma Account DIRESIMULATOR temporária automaticamente."
        ),
    )
    regional = regional_comercial_padrao()

    if st.button("Consultar", type="primary", use_container_width=True, key="btn_consultar"):
        texto = cpf_entrada.strip()
        if not texto:
            st.warning("Informe o CPF do cliente para continuar.")
        else:
            cpf_digitos = normalizar_cpf(texto)
            if len(cpf_digitos) != 11:
                st.warning("O CPF deve conter 11 dígitos.")
            else:
                if st.session_state.sf is None:
                    if "salesforce" in st.secrets:
                        sec = st.secrets["salesforce"]
                        os.environ["SALESFORCE_USER"] = sec.get("USER", "")
                        os.environ["SALESFORCE_PASSWORD"] = sec.get("PASSWORD", "")
                        os.environ["SALESFORCE_TOKEN"] = sec.get("TOKEN", "")
                        if sec.get("REGIONAL_COMERCIAL"):
                            os.environ["SALESFORCE_REGIONAL_COMERCIAL"] = str(
                                sec.get("REGIONAL_COMERCIAL")
                            ).strip()
                    with st.spinner("Conectando ao Salesforce..."):
                        sf = conectar_salesforce()
                    if not sf:
                        st.error(
                            "Não foi possível conectar ao Salesforce. "
                            "Verifique a configuração das credenciais."
                        )
                    else:
                        st.session_state.sf = sf

                if st.session_state.sf is not None:
                    barra = TqdmDirecional(
                        total=total_passos_consulta(forcar_atualizacao),
                        desc=(
                            "Atualizando ranking via Risk3..."
                            if forcar_atualizacao
                            else "Consultando ranking..."
                        ),
                    )
                    try:
                        resultado = consultar_ranking(
                            st.session_state.sf,
                            texto,
                            forcar_atualizacao=forcar_atualizacao,
                            regional_comercial=regional,
                            timeout_seg=TIMEOUT_CONSULTA_SEG,
                            intervalo_seg=INTERVALO_POLL_SEG,
                            barra_progresso=barra,
                        )
                    finally:
                        barra.finish()
                        barra.close()
                    if not resultado.ok and not resultado.ranking:
                        st.markdown(
                            f"""
<div style="margin-top:16px; padding:12px 16px; border-radius:8px;
            border:1px solid {COR_VERMELHO}; background:#fff5f5;
            color:{COR_VERMELHO}; font-weight:600; text-align:center;">
{resultado.mensagem}
</div>
                            """,
                            unsafe_allow_html=True,
                        )
                        st.session_state.ultimo_resultado = None
                    else:
                        st.session_state.ultimo_resultado = {
                            "ranking_conta": resultado.ranking,
                        }
                        if resultado.atualizacao_erro:
                            st.warning(resultado.atualizacao_erro)
                        elif forcar_atualizacao and not resultado.ranking:
                            st.info(resultado.mensagem)

    # Exibição dos dados logo abaixo do botão, dentro do mesmo card
    dados = st.session_state.ultimo_resultado
    if dados:
        st.markdown(
            f"""
<div class="hover-card">
  <div class="hover-card-label">Ranking do Cliente</div>
  <div class="hover-card-value">{dados.get('ranking_conta') or '—'}</div>
</div>
            """,
            unsafe_allow_html=True,
        )

    st.markdown('<div class="footer">Direcional Engenharia</div>', unsafe_allow_html=True)


if __name__ == "__main__":
    main()

