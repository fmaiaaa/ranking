# -*- coding: utf-8 -*-
"""
Consulta de Ranking do Cliente — Direcional
Layout alinhado ao velocimetro.py (Ficha Direcional).

Fluxo: localiza Account; dispara Risk3 e polling REST; SOQL só como fallback.
Sem conta, cria DIRESIMULATOR temporária, consulta e remove ao final.

Logos esperadas na raiz do repositório (mesmos nomes do velocímetro):
  - 502.57_LOGO DIRECIONAL_V2F-01.png
  - 502.57_LOGO D_COR_V3F.png
  - fundo_cadastrorh.jpg (opcional)
"""

from __future__ import annotations

import base64
import html
import os
from pathlib import Path

import streamlit as st
from tqdm import tqdm

from ranking_cliente import consultar_ranking, normalizar_cpf, regional_comercial_padrao
from salesforce_api import conectar_salesforce

_DIR_APP = Path(__file__).resolve().parent
LOGO_TOPO_ARQUIVO = "502.57_LOGO DIRECIONAL_V2F-01.png"
FAVICON_ARQUIVO = "502.57_LOGO D_COR_V3F.png"
FUNDO_CADASTRO_ARQUIVO = "fundo_cadastrorh.jpg"

COR_AZUL_ESC = "#04428f"
COR_VERMELHO = "#cb0935"
COR_VERMELHO_ESCURO = "#9e0828"
COR_BORDA = "#eef2f6"
COR_TEXTO_PRETO = "#000000"
COR_TEXTO_MUTED = "#64748b"
COR_INPUT_BG = "#f0f2f6"
TIMEOUT_CONSULTA_SEG = 90.0
INTERVALO_POLL_SEG = 5.0


def _hex_rgb_triplet(hex_color: str) -> str:
    x = (hex_color or "").strip().lstrip("#")
    if len(x) != 6:
        return "0, 0, 0"
    return f"{int(x[0:2], 16)}, {int(x[2:4], 16)}, {int(x[4:6], 16)}"


RGB_AZUL_CSS = _hex_rgb_triplet(COR_AZUL_ESC)
RGB_VERMELHO_CSS = _hex_rgb_triplet(COR_VERMELHO)


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
    <span class="direcional-tqdm-pct-wrap">
      <span class="direcional-tqdm-spinner" aria-hidden="true"></span>
      <span class="direcional-tqdm-pct">{pct:.0f}%</span>
    </span>
  </div>
  <div class="direcional-tqdm-track">
    <div class="direcional-tqdm-fill" style="width:{pct:.1f}%;"></div>
  </div>
</div>
            """,
            unsafe_allow_html=True,
        )


def total_passos_consulta() -> int:
    poll_passos = int(TIMEOUT_CONSULTA_SEG / INTERVALO_POLL_SEG)
    return poll_passos + 4


def _resolver_png_raiz(nome: str) -> Path | None:
    for base in (_DIR_APP, _DIR_APP.parent):
        p = base / nome
        if p.is_file():
            return p
    return None


def _resolver_imagem_fundo_local(nome: str) -> Path | None:
    for base in (_DIR_APP, _DIR_APP.parent):
        for ext in (".jpg", ".jpeg", ".JPG", ".JPEG", ".png", ".PNG"):
            stem = Path(nome).stem
            p = base / f"{stem}{ext}"
            if p.is_file():
                return p
        p = base / nome
        if p.is_file():
            return p
    return None


def _css_url_fundo_cadastro() -> str:
    p = _resolver_imagem_fundo_local(FUNDO_CADASTRO_ARQUIVO)
    if p and p.is_file():
        try:
            raw = p.read_bytes()
            suf = p.suffix.lower()
            mime = "image/jpeg" if suf in (".jpg", ".jpeg") else "image/png"
            b64 = base64.b64encode(raw).decode("ascii")
            return f"data:{mime};base64,{b64}"
        except OSError:
            pass
    return (
        "https://images.unsplash.com/photo-1486406146926-c627a92ad1ab"
        "?auto=format&fit=crop&w=1920&q=80"
    )


def _logo_arquivo_local() -> str | None:
    p_topo = _resolver_png_raiz(LOGO_TOPO_ARQUIVO)
    if p_topo:
        return str(p_topo)
    for name in ("logo_direcional.png", "logo_direcional.jpg", "logo_direcional.jpeg", "logo.png"):
        p = _DIR_APP / "assets" / name
        if p.is_file():
            return str(p)
    return None


def _logo_url_secrets() -> str | None:
    try:
        if hasattr(st, "secrets"):
            b = st.secrets.get("branding")
            if isinstance(b, dict):
                u = (b.get("LOGO_URL") or "").strip()
                if u:
                    return u
    except Exception:
        pass
    return None


def _exibir_logo_topo() -> None:
    path = _logo_arquivo_local()
    url = _logo_url_secrets()
    try:
        if path:
            ext = Path(path).suffix.lower().lstrip(".")
            mime = "image/png" if ext == "png" else "image/jpeg" if ext in ("jpg", "jpeg") else "image/png"
            with open(path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
            st.markdown(
                f'<div class="ficha-logo-wrap"><img src="data:{mime};base64,{b64}" alt="Direcional" /></div>',
                unsafe_allow_html=True,
            )
            return
        if url:
            st.markdown(
                f'<div class="ficha-logo-wrap"><img src="{html.escape(url)}" alt="Direcional" /></div>',
                unsafe_allow_html=True,
            )
    except Exception:
        pass


def _cabecalho_pagina() -> None:
    _exibir_logo_topo()
    st.markdown(
        f'<div class="ficha-hero-stack">'
        f'<div class="ficha-hero">'
        f'<p class="ficha-title">Consulta de Ranking</p>'
        f'<p class="ficha-subtitle">developed by Lucas Maia</p>'
        f"</div>"
        f'<div class="ficha-hero-bar-wrap" aria-hidden="true">'
        f'<div class="ficha-hero-bar"></div>'
        f"</div>"
        f"</div>",
        unsafe_allow_html=True,
    )


def aplicar_estilo() -> None:
    bg_url = _css_url_fundo_cadastro()
    st.markdown(
        f"""
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Montserrat:wght@400;600;700;800;900&family=Inter:wght@400;500;600;700&display=swap');
        @keyframes fichaFadeIn {{
            from {{ opacity: 0; transform: translateY(18px); }}
            to {{ opacity: 1; transform: translateY(0); }}
        }}
        @keyframes fichaShimmer {{
            0% {{ background-position: 0% 50%; }}
            100% {{ background-position: 200% 50%; }}
        }}
        @keyframes direcionalSpin {{
            to {{ transform: rotate(360deg); }}
        }}
        html, body, :root, [data-testid="stApp"] {{
            color-scheme: light !important;
        }}
        html, body {{
            font-family: 'Inter', sans-serif;
            color: {COR_TEXTO_PRETO};
            background: transparent !important;
        }}
        .stApp,
        [data-testid="stApp"] {{
            background:
                linear-gradient(135deg, rgba({RGB_AZUL_CSS}, 0.82) 0%, rgba(30, 58, 95, 0.55) 38%, rgba({RGB_VERMELHO_CSS}, 0.22) 72%, rgba(15, 23, 42, 0.45) 100%),
                url("{bg_url}") center / cover no-repeat !important;
            background-attachment: scroll !important;
        }}
        [data-testid="stAppViewContainer"] {{
            background: transparent !important;
        }}
        header[data-testid="stHeader"],
        [data-testid="stHeader"] {{
            background: transparent !important;
            border: none !important;
            box-shadow: none !important;
        }}
        [data-testid="stDecoration"] {{ display: none !important; }}
        [data-testid="stSidebar"] {{ display: none !important; }}
        [data-testid="stSidebarCollapsedControl"] {{ display: none !important; }}
        [data-testid="stMain"] {{
            padding-left: clamp(14px, 4vw, 40px) !important;
            padding-right: clamp(14px, 4vw, 40px) !important;
            padding-top: clamp(16px, 3vh, 32px) !important;
            padding-bottom: clamp(16px, 3vh, 32px) !important;
            display: flex !important;
            align-items: center !important;
            justify-content: center !important;
            min-height: calc(100vh - 4rem) !important;
        }}
        section.main > div {{
            width: 100%;
            display: flex !important;
            flex-direction: column !important;
            align-items: center !important;
            justify-content: center !important;
        }}
        .block-container {{
            max-width: 920px !important;
            width: min(920px, 94vw) !important;
            margin: auto !important;
            padding: 2.75rem 3rem 3rem 3rem !important;
            min-height: 520px !important;
            background: rgba(255, 255, 255, 0.82) !important;
            backdrop-filter: blur(18px) saturate(1.15);
            -webkit-backdrop-filter: blur(18px) saturate(1.15);
            border-radius: 28px !important;
            border: 1px solid rgba(255, 255, 255, 0.45) !important;
            box-shadow:
                0 4px 6px -1px rgba({RGB_AZUL_CSS}, 0.06),
                0 24px 48px -12px rgba({RGB_AZUL_CSS}, 0.18),
                inset 0 1px 0 rgba(255, 255, 255, 0.55) !important;
            animation: fichaFadeIn 0.7s cubic-bezier(0.22, 1, 0.36, 1) both;
        }}
        .ficha-logo-wrap {{
            text-align: center;
            padding: 0.25rem 0 0.75rem 0;
        }}
        .ficha-logo-wrap img {{
            max-height: 92px;
            width: auto;
            max-width: min(320px, 88vw);
            object-fit: contain;
            display: inline-block;
        }}
        .ficha-hero-stack {{
            width: 100%;
            margin-bottom: 0.35rem;
        }}
        .ficha-hero {{
            text-align: center;
            padding: 0.5rem 0 0 0;
            margin: 0 auto;
            max-width: 640px;
            animation: fichaFadeIn 0.85s cubic-bezier(0.22, 1, 0.36, 1) 0.1s both;
        }}
        .ficha-hero .ficha-title {{
            font-family: 'Montserrat', sans-serif;
            font-size: clamp(1.55rem, 3.8vw, 2rem);
            font-weight: 900;
            color: {COR_AZUL_ESC};
            margin: 0;
            line-height: 1.25;
            letter-spacing: -0.02em;
        }}
        .ficha-hero .ficha-subtitle {{
            font-family: 'Inter', sans-serif;
            font-size: 0.92rem;
            font-weight: 500;
            color: {COR_TEXTO_MUTED};
            margin: 0.55rem 0 0 0;
            letter-spacing: 0.04em;
            text-transform: lowercase;
        }}
        .ficha-hero-bar-wrap {{
            width: 100%;
            margin: clamp(1rem, 2.8vw, 1.45rem) 0 1.75rem;
        }}
        .ficha-hero-bar {{
            height: 4px;
            width: 100%;
            border-radius: 999px;
            background: linear-gradient(90deg, {COR_AZUL_ESC}, {COR_VERMELHO}, {COR_AZUL_ESC});
            background-size: 200% 100%;
            animation: fichaShimmer 4s ease-in-out infinite alternate;
        }}
        div[data-baseweb="input"] {{
            border-radius: 12px !important;
            border: 1px solid #e2e8f0 !important;
            background-color: {COR_INPUT_BG} !important;
            min-height: 54px !important;
        }}
        div[data-baseweb="input"] input {{
            font-size: 1.08rem !important;
            padding-top: 14px !important;
            padding-bottom: 14px !important;
        }}
        [data-testid="stTextInput"] label p,
        [data-testid="stWidgetLabel"] p {{
            font-size: 1rem !important;
            font-weight: 600 !important;
            margin-bottom: 0.45rem !important;
        }}
        .stButton button {{
            font-family: 'Inter', sans-serif;
            border-radius: 12px !important;
            width: 100% !important;
            height: 54px !important;
            min-height: 54px !important;
            font-size: 1rem !important;
            font-weight: 700 !important;
            text-transform: uppercase;
            letter-spacing: 0.06em;
            margin-top: 0.35rem !important;
        }}
        .stButton button[kind="primary"] {{
            background: {COR_VERMELHO} !important;
            color: #ffffff !important;
            border: none !important;
        }}
        .stButton button[kind="primary"]:hover {{
            background: {COR_VERMELHO_ESCURO} !important;
        }}
        .ranking-kpi {{
            background: linear-gradient(180deg, rgba(255,255,255,0.95) 0%, rgba(250,251,252,0.9) 100%);
            border: 1px solid rgba(226, 232, 240, 0.9);
            border-radius: 16px;
            padding: 32px 24px;
            min-height: 140px;
            text-align: center;
            box-shadow: 0 2px 8px rgba({RGB_AZUL_CSS}, 0.06);
            transition: transform 0.3s ease, box-shadow 0.3s ease;
            margin-top: 1rem;
            display: flex;
            flex-direction: column;
            justify-content: center;
        }}
        .ranking-kpi:hover {{
            transform: translateY(-4px);
            box-shadow: 0 10px 20px -5px rgba({RGB_AZUL_CSS}, 0.15);
        }}
        .ranking-kpi .lbl {{
            font-size: 0.82rem;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.1em;
            color: {COR_TEXTO_MUTED};
        }}
        .ranking-kpi .val {{
            font-family: 'Montserrat', sans-serif;
            font-size: 1.65rem;
            font-weight: 800;
            color: {COR_VERMELHO} !important;
            margin-top: 12px;
            word-break: break-word;
        }}
        .direcional-tqdm {{
            margin: 1.35rem 0 1.5rem;
        }}
        .direcional-tqdm-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 0.65rem;
            font-size: 1rem;
            font-weight: 600;
            color: {COR_AZUL_ESC};
        }}
        .direcional-tqdm-pct-wrap {{
            display: inline-flex;
            align-items: center;
            gap: 0.55rem;
        }}
        .direcional-tqdm-spinner {{
            width: 16px;
            height: 16px;
            border: 2.5px solid rgba({RGB_AZUL_CSS}, 0.2);
            border-top-color: {COR_VERMELHO};
            border-radius: 50%;
            animation: direcionalSpin 0.75s linear infinite;
            flex-shrink: 0;
        }}
        .direcional-tqdm-pct {{
            color: {COR_VERMELHO};
            font-weight: 800;
            font-size: 1.05rem;
            min-width: 2.75rem;
            text-align: right;
        }}
        .direcional-tqdm-track {{
            width: 100%;
            height: 16px;
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
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def _resolver_favicon() -> str | None:
    fav = _resolver_png_raiz(FAVICON_ARQUIVO)
    if fav:
        return str(fav)
    fallback = _DIR_APP / "favicon.png"
    return str(fallback) if fallback.is_file() else None


def main() -> None:
    st.set_page_config(
        page_title="Consulta de Ranking | Direcional",
        page_icon=_resolver_favicon(),
        layout="wide",
    )
    aplicar_estilo()
    _cabecalho_pagina()

    if "sf" not in st.session_state:
        st.session_state.sf = None
    if "ultimo_resultado" not in st.session_state:
        st.session_state.ultimo_resultado = None

    cpf_entrada = st.text_input("CPF do cliente", value="", placeholder="Ex.: 000.000.000-00")
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
                        total=total_passos_consulta(),
                        desc="Consultando ranking...",
                    )
                    try:
                        resultado = consultar_ranking(
                            st.session_state.sf,
                            texto,
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
<div style="margin-top:16px; padding:12px 16px; border-radius:10px;
            border:1px solid {COR_VERMELHO}; background:#fff5f5;
            color:{COR_VERMELHO}; font-weight:600; text-align:center;">
{html.escape(resultado.mensagem or "Consulta sem resultado.")}
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
                        elif not resultado.ranking:
                            st.info(resultado.mensagem)

    dados = st.session_state.ultimo_resultado
    if dados:
        ranking_txt = html.escape(str(dados.get("ranking_conta") or "—"))
        st.markdown(
            f"""
<div class="ranking-kpi">
  <div class="lbl">Ranking do Cliente</div>
  <div class="val">{ranking_txt}</div>
</div>
            """,
            unsafe_allow_html=True,
        )


if __name__ == "__main__":
    main()
