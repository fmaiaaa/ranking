# -*- coding: utf-8 -*-
"""
Consulta e atualização de ranking de cliente no Salesforce (Account.Ranking__c).

Leitura via Apex REST UpdateRankingRest (GET /update-ranking?cpf=...).
Nova consulta via ConsultorRankingRisk3REST + IntegracaoRisk3 (Risk3).
Fallback de leitura: SOQL direto na Account.
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

APEX_REST_UPDATE_RANKING = "update-ranking"
APEX_REST_RANKING = "consulta-ranking/v1"
QA_ACCOUNT = "sobjects/Account/quickActions/Consultar_Status_CPF"
QA_OPPORTUNITY = "sobjects/Opportunity/quickActions/Consultar_Status_CPFOP"

ERRO_APEX_NAO_IMPLANTADO = (
    "Endpoint Apex /services/apexrest/consulta-ranking/v1 não encontrado. "
    "Implante as classes ConsultorRankingRisk3 e ConsultorRankingRisk3REST no Salesforce."
)

ERRO_SCREEN_FLOW_API = (
    "Fallback de quick action indisponível (Screen Flow). "
    "Use o endpoint Apex Risk3 após implantar as classes do repositório."
)


def normalizar_cpf(valor: str) -> str:
    if not valor:
        return ""
    return re.sub(r"\D+", "", str(valor))


def cpf_mascarado(cpf_digitos: str) -> str:
    d = normalizar_cpf(cpf_digitos)
    if len(d) != 11:
        raise ValueError("CPF deve conter 11 dígitos.")
    return f"{d[0:3]}.{d[3:6]}.{d[6:9]}-{d[9:11]}"


def regional_comercial_padrao() -> str:
    return (os.environ.get("SALESFORCE_REGIONAL_COMERCIAL") or "RJ").strip().upper() or "RJ"


def _escape_soql(valor: str) -> str:
    return str(valor).replace("\\", "\\\\").replace("'", "\\'")


@dataclass
class ResultadoRanking:
    ok: bool
    cpf: str
    account_id: Optional[str] = None
    account_name: Optional[str] = None
    ranking: Optional[str] = None
    ranking_score: Optional[float] = None
    ultima_consulta_cpf: Optional[str] = None
    opportunity_id: Optional[str] = None
    mensagem: str = ""
    atualizacao_solicitada: bool = False
    atualizacao_disparada: bool = False
    atualizacao_erro: Optional[str] = None
    tentativas_disparo: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _parse_resposta_update_ranking(texto: str) -> Tuple[Optional[str], Optional[str]]:
    resposta = (texto or "").strip()
    if resposta.startswith("Ranking:"):
        ranking = resposta[len("Ranking:") :].strip()
        if ranking.lower() in ("", "null", "none"):
            return None, None
        return ranking, None
    if resposta == "Conta não encontrada.":
        return None, "Nenhuma conta encontrada para o CPF informado."
    if resposta:
        return None, resposta
    return None, "Resposta vazia do endpoint UpdateRankingRest."


def _url_apex_rest(sf, resource: str) -> str:
    base = (getattr(sf, "base_url", "") or "").split("/services/data")[0].rstrip("/")
    if not base and hasattr(sf, "sf_instance"):
        base = f"https://{sf.sf_instance}"
    recurso = resource.lstrip("/")
    if recurso.startswith("apexrest/"):
        recurso = recurso[len("apexrest/") :]
    return f"{base}/services/apexrest/{recurso}"


def buscar_ranking_via_rest(
    sf,
    cpf_bruto: str,
) -> Tuple[Optional[str], Optional[str], List[str]]:
    """
    GET UpdateRankingRest — endpoint já implantado na org Direcional.
    Retorna (ranking, erro, tentativas).
    """
    cpf_digitos = normalizar_cpf(cpf_bruto)
    if len(cpf_digitos) != 11:
        return None, "Informe um CPF válido com 11 dígitos.", []

    tentativas: List[str] = []
    candidatos = (cpf_mascarado(cpf_digitos), cpf_digitos)
    ultimo_erro: Optional[str] = None

    for cpf_param in candidatos:
        tentativas.append(f"GET /services/apexrest/{APEX_REST_UPDATE_RANKING}?cpf={cpf_param}")
        try:
            url = _url_apex_rest(sf, APEX_REST_UPDATE_RANKING)
            resp = sf.session.get(
                url,
                params={"cpf": cpf_param},
                headers={"Authorization": f"Bearer {sf.session_id}"},
                timeout=30,
            )
            resp.raise_for_status()
            ranking, erro = _parse_resposta_update_ranking(resp.text)
            if ranking:
                return ranking, None, tentativas
            if erro and "não encontrada" not in erro.lower():
                return None, erro, tentativas
            ultimo_erro = erro
        except Exception as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status == 403:
                return None, None, tentativas
            texto = str(exc)
            if "403" in texto:
                return None, None, tentativas
            if "404" in texto or "NOT_FOUND" in texto:
                return None, None, tentativas
            return None, texto, tentativas

    return None, ultimo_erro or "Nenhuma conta encontrada para o CPF informado.", tentativas


def aguardar_ranking_via_rest(
    sf,
    cpf_bruto: str,
    *,
    timeout_seg: float = 90.0,
    intervalo_seg: float = 5.0,
    ranking_anterior: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    fim = time.monotonic() + timeout_seg
    ultimo_ranking: Optional[str] = None
    ultimo_erro: Optional[str] = None

    while time.monotonic() < fim:
        ranking, erro, _ = buscar_ranking_via_rest(sf, cpf_bruto)
        ultimo_ranking = ranking
        ultimo_erro = erro
        if ranking and ranking != ranking_anterior:
            return ranking, None
        if ranking and not ranking_anterior:
            return ranking, None
        time.sleep(intervalo_seg)

    return ultimo_ranking, ultimo_erro


def buscar_conta_por_cpf(sf, cpf_bruto: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    cpf_digitos = normalizar_cpf(cpf_bruto)
    if len(cpf_digitos) != 11:
        return None, "Informe um CPF válido com 11 dígitos."

    mascara = cpf_mascarado(cpf_digitos)
    soql = f"""
        SELECT
            Id,
            Name,
            CPF__c,
            Ranking__c,
            Ranking_Score__c,
            UltimaConsultaCPF__c,
            LastModifiedDate
        FROM Account
        WHERE CPF__c = '{_escape_soql(mascara)}'
        ORDER BY LastModifiedDate DESC
        LIMIT 1
    """
    try:
        res = sf.query(soql)
        registros = res.get("records") or []
        if registros:
            return registros[0], None

        soql_digits = f"""
            SELECT Id, Name, CPF__c, Ranking__c, Ranking_Score__c,
                   UltimaConsultaCPF__c, LastModifiedDate
            FROM Account
            WHERE CPF__c = '{_escape_soql(cpf_digitos)}'
            ORDER BY LastModifiedDate DESC
            LIMIT 1
        """
        res2 = sf.query(soql_digits)
        registros2 = res2.get("records") or []
        if registros2:
            return registros2[0], None
        return None, "Nenhuma conta encontrada para o CPF informado."
    except Exception as exc:
        return None, f"Erro ao consultar o Salesforce: {exc}"


def buscar_oportunidade_recente(sf, account_id: str) -> Optional[str]:
    soql = f"""
        SELECT Id
        FROM Opportunity
        WHERE AccountId = '{_escape_soql(account_id)}'
        ORDER BY CreatedDate DESC
        LIMIT 1
    """
    try:
        res = sf.query(soql)
        registros = res.get("records") or []
        return registros[0]["Id"] if registros else None
    except Exception:
        return None


def ler_ranking_conta(sf, account_id: str) -> Dict[str, Any]:
    soql = f"""
        SELECT Id, Name, CPF__c, Ranking__c, Ranking_Score__c,
               UltimaConsultaCPF__c, LastModifiedDate
        FROM Account
        WHERE Id = '{_escape_soql(account_id)}'
        LIMIT 1
    """
    res = sf.query(soql)
    registros = res.get("records") or []
    if not registros:
        raise ValueError(f"Conta não encontrada: {account_id}")
    return registros[0]


def consultar_ranking_risk3(
    sf,
    cpf_bruto: str,
    regional_comercial: Optional[str] = None,
) -> Tuple[bool, Optional[str], str, Optional[str], List[str]]:
    """
    Chama IntegracaoRisk3 via Apex REST.
    Retorna (ok, ranking, mensagem, account_id, tentativas).
    """
    cpf_digitos = normalizar_cpf(cpf_bruto)
    regional = (regional_comercial or regional_comercial_padrao()).strip().upper()
    payload = {"cpf": cpf_digitos, "regionalComercial": regional}
    tentativa = f"POST /services/apexrest/{APEX_REST_RANKING} {payload}"

    try:
        url = _url_apex_rest(sf, APEX_REST_RANKING)
        resp = sf.session.post(
            url,
            json=payload,
            headers={
                "Authorization": f"Bearer {sf.session_id}",
                "Content-Type": "application/json",
            },
            timeout=60,
        )
        resp.raise_for_status()
        resposta = resp.json()
        ranking = resposta.get("ranking")
        mensagem = resposta.get("mensagem") or resposta.get("resultadoIntegracao") or ""
        ok = bool(resposta.get("ok")) or bool(ranking)
        account_id = resposta.get("accountId")
        return ok, ranking, mensagem, account_id, [tentativa]
    except Exception as exc:
        texto = str(exc)
        if "NOT_FOUND" in texto or "404" in texto:
            return False, None, ERRO_APEX_NAO_IMPLANTADO, None, [tentativa]
        return False, None, texto, None, [tentativa]


def _interpretar_erro_disparo(exc: Exception) -> str:
    texto = str(exc)
    if "-1577168114" in texto or "UNKNOWN_EXCEPTION" in texto:
        return ERRO_SCREEN_FLOW_API
    return texto


def disparar_consulta_ranking(
    sf,
    account_id: str,
    opportunity_id: Optional[str] = None,
) -> Tuple[bool, Optional[str], List[str]]:
    """Fallback legado: quick actions (Screen Flow)."""
    tentativas: List[str] = []
    payloads = [
        {"contextId": account_id},
        {"recordId": account_id},
        {"inputs": [{"name": "recordId", "value": account_id}]},
    ]

    for payload in payloads:
        label = f"POST {QA_ACCOUNT} {payload}"
        tentativas.append(label)
        try:
            sf.restful(QA_ACCOUNT, method="POST", json=payload)
            return True, None, tentativas
        except Exception as exc:
            err = _interpretar_erro_disparo(exc)
            if err != ERRO_SCREEN_FLOW_API:
                return False, err, tentativas

    if opportunity_id:
        for payload in ({"contextId": opportunity_id}, {"recordId": opportunity_id}):
            label = f"POST {QA_OPPORTUNITY} {payload}"
            tentativas.append(label)
            try:
                sf.restful(QA_OPPORTUNITY, method="POST", json=payload)
                return True, None, tentativas
            except Exception as exc:
                err = _interpretar_erro_disparo(exc)
                if err != ERRO_SCREEN_FLOW_API:
                    return False, err, tentativas

    return False, ERRO_SCREEN_FLOW_API, tentativas


def aguardar_atualizacao_ranking(
    sf,
    account_id: str,
    *,
    timeout_seg: float = 90.0,
    intervalo_seg: float = 5.0,
    ranking_anterior: Optional[str] = None,
    ultima_consulta_anterior: Optional[str] = None,
) -> Dict[str, Any]:
    fim = time.monotonic() + timeout_seg
    ultimo = ler_ranking_conta(sf, account_id)

    while time.monotonic() < fim:
        atual = ler_ranking_conta(sf, account_id)
        ranking_atual = atual.get("Ranking__c")
        ultima_atual = atual.get("UltimaConsultaCPF__c")

        mudou_ranking = ranking_atual and ranking_atual != ranking_anterior
        mudou_data = ultima_atual and ultima_atual != ultima_consulta_anterior
        preencheu_ranking = ranking_atual and not ranking_anterior

        if mudou_ranking or mudou_data or preencheu_ranking:
            return atual

        time.sleep(intervalo_seg)
        ultimo = atual

    return ultimo


def _montar_resultado(conta: Dict[str, Any], cpf_digitos: str, **extra) -> ResultadoRanking:
    ranking = conta.get("Ranking__c")
    return ResultadoRanking(
        ok=bool(ranking),
        cpf=cpf_digitos,
        account_id=conta.get("Id"),
        account_name=conta.get("Name"),
        ranking=ranking,
        ranking_score=conta.get("Ranking_Score__c"),
        ultima_consulta_cpf=conta.get("UltimaConsultaCPF__c"),
        mensagem="Ranking encontrado." if ranking else "Conta encontrada, mas sem ranking cadastrado.",
        **extra,
    )


def _resultado_de_ranking_direto(
    cpf_digitos: str,
    ranking: Optional[str],
    mensagem: str,
    account_id: Optional[str],
    *,
    atualizacao_solicitada: bool,
    tentativas: List[str],
    disparada: bool,
    erro: Optional[str] = None,
) -> ResultadoRanking:
    conta: Dict[str, Any] = {
        "Id": account_id,
        "Name": None,
        "Ranking__c": ranking,
        "Ranking_Score__c": None,
        "UltimaConsultaCPF__c": None,
    }
    resultado = _montar_resultado(
        conta,
        cpf_digitos,
        atualizacao_solicitada=atualizacao_solicitada,
        atualizacao_disparada=disparada,
        atualizacao_erro=erro,
        tentativas_disparo=tentativas,
    )
    resultado.mensagem = mensagem or resultado.mensagem
    resultado.ok = bool(ranking)
    return resultado


def consultar_ranking(
    sf,
    cpf_bruto: str,
    *,
    forcar_atualizacao: bool = False,
    regional_comercial: Optional[str] = None,
    timeout_seg: float = 90.0,
    intervalo_seg: float = 5.0,
) -> ResultadoRanking:
    cpf_digitos = normalizar_cpf(cpf_bruto)
    if len(cpf_digitos) != 11:
        return ResultadoRanking(ok=False, cpf=cpf_digitos, mensagem="Informe um CPF válido com 11 dígitos.")

    conta, erro = buscar_conta_por_cpf(sf, cpf_bruto)
    opp_id = buscar_oportunidade_recente(sf, conta["Id"]) if conta else None

    if forcar_atualizacao:
        ok_r3, ranking_r3, msg_r3, acc_r3, tentativas = consultar_ranking_risk3(
            sf, cpf_bruto, regional_comercial
        )
        if ranking_r3 or ok_r3:
            if acc_r3:
                try:
                    conta_atual = ler_ranking_conta(sf, acc_r3)
                    return _montar_resultado(
                        conta_atual,
                        cpf_digitos,
                        opportunity_id=opp_id,
                        atualizacao_solicitada=True,
                        atualizacao_disparada=True,
                        tentativas_disparo=tentativas,
                        mensagem=msg_r3 or "Ranking atualizado via Risk3.",
                    )
                except Exception:
                    pass
            return _resultado_de_ranking_direto(
                cpf_digitos,
                ranking_r3,
                msg_r3 or "Consulta Risk3 executada.",
                acc_r3,
                atualizacao_solicitada=True,
                tentativas=tentativas,
                disparada=True,
            )

        ranking_poll, err_poll = aguardar_ranking_via_rest(
            sf,
            cpf_bruto,
            timeout_seg=timeout_seg,
            intervalo_seg=intervalo_seg,
            ranking_anterior=conta.get("Ranking__c") if conta else None,
        )
        if ranking_poll:
            return _resultado_de_ranking_direto(
                cpf_digitos,
                ranking_poll,
                "Ranking atualizado após consulta Risk3.",
                conta.get("Id") if conta else acc_r3,
                atualizacao_solicitada=True,
                tentativas=tentativas,
                disparada=True,
            )

        if ERRO_APEX_NAO_IMPLANTADO not in msg_r3 and conta:
            disparou, err, tentativas_qa = disparar_consulta_ranking(sf, conta["Id"], opp_id)
            tentativas.extend(tentativas_qa)
            if disparou:
                atualizada = aguardar_atualizacao_ranking(
                    sf,
                    conta["Id"],
                    timeout_seg=timeout_seg,
                    intervalo_seg=intervalo_seg,
                    ranking_anterior=conta.get("Ranking__c"),
                    ultima_consulta_anterior=conta.get("UltimaConsultaCPF__c"),
                )
                resultado = _montar_resultado(
                    atualizada,
                    cpf_digitos,
                    opportunity_id=opp_id,
                    atualizacao_solicitada=True,
                    atualizacao_disparada=True,
                    tentativas_disparo=tentativas,
                )
                if resultado.ranking:
                    resultado.ok = True
                    resultado.mensagem = "Ranking atualizado após nova consulta."
                else:
                    resultado.mensagem = (
                        "Consulta disparada, mas o ranking não foi atualizado dentro do tempo de espera."
                    )
                return resultado
            return ResultadoRanking(
                ok=bool(conta.get("Ranking__c")),
                cpf=cpf_digitos,
                account_id=conta.get("Id"),
                ranking=conta.get("Ranking__c"),
                mensagem=f"Nova consulta não pôde ser disparada. {err or msg_r3}",
                atualizacao_solicitada=True,
                atualizacao_disparada=False,
                atualizacao_erro=err or msg_r3,
                tentativas_disparo=tentativas,
            )

        if conta:
            return ResultadoRanking(
                ok=bool(conta.get("Ranking__c")),
                cpf=cpf_digitos,
                account_id=conta.get("Id"),
                ranking=conta.get("Ranking__c"),
                mensagem=msg_r3,
                atualizacao_solicitada=True,
                atualizacao_disparada=False,
                atualizacao_erro=msg_r3,
                tentativas_disparo=tentativas,
            )
        return ResultadoRanking(
            ok=False,
            cpf=cpf_digitos,
            mensagem=msg_r3,
            atualizacao_solicitada=True,
            atualizacao_disparada=False,
            atualizacao_erro=msg_r3,
            tentativas_disparo=tentativas,
        )

    ranking_rest, err_rest, tentativas_rest = buscar_ranking_via_rest(sf, cpf_bruto)
    if ranking_rest:
        conta_ref, _ = buscar_conta_por_cpf(sf, cpf_bruto)
        opp = buscar_oportunidade_recente(sf, conta_ref["Id"]) if conta_ref else None
        return ResultadoRanking(
            ok=True,
            cpf=cpf_digitos,
            account_id=conta_ref.get("Id") if conta_ref else None,
            ranking=ranking_rest,
            opportunity_id=opp,
            mensagem="Ranking encontrado.",
            tentativas_disparo=tentativas_rest,
        )

    if err_rest and "não encontrada" in err_rest.lower():
        return ResultadoRanking(ok=False, cpf=cpf_digitos, mensagem=err_rest)

    if erro or not conta:
        return ResultadoRanking(
            ok=False,
            cpf=cpf_digitos,
            mensagem=err_rest or erro or "Conta não encontrada.",
        )

    return _montar_resultado(conta, cpf_digitos, opportunity_id=opp_id)
