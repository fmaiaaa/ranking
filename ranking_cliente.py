# -*- coding: utf-8 -*-
"""
Consulta e atualização de ranking no Salesforce.

- Leitura: Apex REST UpdateRankingRest (GET /update-ranking?cpf=...)
- Atualização: IntegracaoRisk3 (@InvocableMethod consultarStatusCPFCNPJ)
- Fallback de leitura: SOQL na Account
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

APEX_REST_UPDATE_RANKING = "update-ranking"
APEX_ACTION_INTEGRACAO_RISK3 = "actions/custom/apex/IntegracaoRisk3"


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


def _url_apex_rest(sf, resource: str) -> str:
    base = (getattr(sf, "base_url", "") or "").split("/services/data")[0].rstrip("/")
    if not base and hasattr(sf, "sf_instance"):
        base = f"https://{sf.sf_instance}"
    recurso = resource.lstrip("/")
    if recurso.startswith("apexrest/"):
        recurso = recurso[len("apexrest/") :]
    return f"{base}/services/apexrest/{recurso}"


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


def _parse_resposta_update_ranking(texto: str) -> Tuple[Optional[str], Optional[str], bool]:
    """
    Retorna (ranking, erro, conta_encontrada).
  ranking None + conta_encontrada True = conta existe sem ranking.
    """
    resposta = (texto or "").strip()
    if resposta.startswith("Ranking:"):
        ranking = resposta[len("Ranking:") :].strip()
        if ranking.lower() in ("", "null", "none"):
            return None, None, True
        return ranking, None, True
    if resposta == "Conta não encontrada.":
        return None, "Nenhuma conta encontrada para o CPF informado.", False
    if resposta == "Informe um dos parâmetros: id_risk3, cpf ou cnpj.":
        return None, resposta, False
    if resposta.startswith("Erro ao buscar ranking:"):
        return None, resposta, False
    if resposta:
        return None, resposta, False
    return None, "Resposta vazia do endpoint UpdateRankingRest.", False


def buscar_ranking_via_rest(
    sf,
    cpf_bruto: str,
) -> Tuple[Optional[str], Optional[str], bool, List[str]]:
    """
    GET UpdateRankingRest.
    Retorna (ranking, erro, conta_encontrada, tentativas).
    """
    cpf_digitos = normalizar_cpf(cpf_bruto)
    if len(cpf_digitos) != 11:
        return None, "Informe um CPF válido com 11 dígitos.", False, []

    tentativas: List[str] = []
    # Testes Apex (UpdateRankingRestTest) usam CPF só com dígitos.
    candidatos = (cpf_digitos, cpf_mascarado(cpf_digitos))
    ultimo_erro: Optional[str] = None
    conta_encontrada = False

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
            if resp.status_code == 403:
                return None, "Sem permissão para executar UpdateRankingRest.", False, tentativas
            resp.raise_for_status()
            ranking, erro, encontrada = _parse_resposta_update_ranking(resp.text)
            conta_encontrada = conta_encontrada or encontrada
            if ranking:
                return ranking, None, True, tentativas
            if erro and "não encontrada" in erro.lower():
                ultimo_erro = erro
                continue
            if encontrada:
                return None, None, True, tentativas
            if erro:
                return None, erro, False, tentativas
        except Exception as exc:
            texto = str(exc)
            if "403" in texto:
                return None, "Sem permissão para executar UpdateRankingRest.", False, tentativas
            return None, texto, False, tentativas

    return None, ultimo_erro or "Nenhuma conta encontrada para o CPF informado.", conta_encontrada, tentativas


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
        ranking, erro, _, _ = buscar_ranking_via_rest(sf, cpf_bruto)
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
    campos = (
        "Id, Name, CPF__c, Ranking__c, Ranking_Score__c, "
        "UltimaConsultaCPF__c, Regional__c, Regional_Comercial__c, LastModifiedDate"
    )
    for cpf_valor in (cpf_digitos, mascara):
        soql = f"""
            SELECT {campos}
            FROM Account
            WHERE CPF__c = '{_escape_soql(cpf_valor)}'
            ORDER BY LastModifiedDate DESC
            LIMIT 1
        """
        try:
            res = sf.query(soql)
            registros = res.get("records") or []
            if registros:
                return registros[0], None
        except Exception as exc:
            return None, f"Erro ao consultar o Salesforce: {exc}"
    return None, "Nenhuma conta encontrada para o CPF informado."


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


def _extrair_mensagem_risk3(resposta: Any) -> str:
    if not resposta:
        return ""
    if isinstance(resposta, str):
        return resposta
    if isinstance(resposta, dict):
        for chave in ("outputValues", "actionResults", "result", "message"):
            if chave in resposta:
                return _extrair_mensagem_risk3(resposta[chave])
        if "result" in resposta:
            return str(resposta.get("result") or "")
    if isinstance(resposta, list) and resposta:
        partes = []
        for item in resposta:
            if isinstance(item, dict):
                partes.append(str(item.get("result") or item.get("outputValues") or item))
            else:
                partes.append(str(item))
        return "; ".join(p for p in partes if p)
    return str(resposta)


def disparar_integracao_risk3(
    sf,
    account_id: str,
    opportunity_id: Optional[str] = None,
    *,
    bypass: bool = True,
) -> Tuple[bool, str, List[str]]:
    """
    Invoca IntegracaoRisk3.consultarStatusCPFCNPJ via Actions API.
    """
    tentativas: List[str] = []
    payloads: List[Dict[str, Any]] = [
        {
            "inputs": [
                {
                    "Account": {"id": account_id},
                    "Opportunity": {"id": opportunity_id} if opportunity_id else None,
                    "bypassRisk3": bypass,
                }
            ]
        },
        {
            "inputs": [
                {
                    "Account": account_id,
                    "bypassRisk3": bypass,
                }
            ]
        },
    ]

    ultimo_erro = ""
    for payload in payloads:
        label = f"POST {APEX_ACTION_INTEGRACAO_RISK3} {json.dumps(payload, ensure_ascii=False)}"
        tentativas.append(label)
        try:
            resposta = sf.restful(APEX_ACTION_INTEGRACAO_RISK3, method="POST", json=payload)
            mensagem = _extrair_mensagem_risk3(resposta)
            return True, mensagem or "IntegracaoRisk3 executada.", tentativas
        except Exception as exc:
            ultimo_erro = str(exc)
            if "NOT_FOUND" in ultimo_erro or "404" in ultimo_erro:
                return False, "Ação Apex IntegracaoRisk3 não encontrada ou sem permissão.", tentativas

    return False, ultimo_erro or "Falha ao invocar IntegracaoRisk3.", tentativas


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
    atualizacao_solicitada: bool = False,
    tentativas: Optional[List[str]] = None,
    disparada: bool = False,
    erro: Optional[str] = None,
) -> ResultadoRanking:
    resultado = ResultadoRanking(
        ok=bool(ranking),
        cpf=cpf_digitos,
        account_id=account_id,
        ranking=ranking,
        mensagem=mensagem,
        atualizacao_solicitada=atualizacao_solicitada,
        atualizacao_disparada=disparada,
        atualizacao_erro=erro,
        tentativas_disparo=tentativas or [],
    )
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
    _ = regional_comercial  # reservado; regional vem dos campos da Account na org
    cpf_digitos = normalizar_cpf(cpf_bruto)
    if len(cpf_digitos) != 11:
        return ResultadoRanking(ok=False, cpf=cpf_digitos, mensagem="Informe um CPF válido com 11 dígitos.")

    conta, erro_soql = buscar_conta_por_cpf(sf, cpf_bruto)
    opp_id = buscar_oportunidade_recente(sf, conta["Id"]) if conta else None

    if forcar_atualizacao:
        if not conta:
            return ResultadoRanking(
                ok=False,
                cpf=cpf_digitos,
                mensagem=(
                    "Não há conta cadastrada com este CPF no Salesforce. "
                    "A IntegracaoRisk3 exige uma Account existente."
                ),
            )

        disparou, msg_r3, tentativas = disparar_integracao_risk3(sf, conta["Id"], opp_id, bypass=True)
        ranking_anterior = conta.get("Ranking__c")

        if disparou:
            ranking_poll, err_poll = aguardar_ranking_via_rest(
                sf,
                cpf_bruto,
                timeout_seg=timeout_seg,
                intervalo_seg=intervalo_seg,
                ranking_anterior=ranking_anterior,
            )
            if not ranking_poll:
                try:
                    atual = ler_ranking_conta(sf, conta["Id"])
                    if atual.get("Ranking__c"):
                        ranking_poll = atual.get("Ranking__c")
                except Exception:
                    pass

            if ranking_poll:
                return _resultado_de_ranking_direto(
                    cpf_digitos,
                    ranking_poll,
                    msg_r3 or "Ranking atualizado via IntegracaoRisk3.",
                    conta["Id"],
                    atualizacao_solicitada=True,
                    tentativas=tentativas,
                    disparada=True,
                )

            return ResultadoRanking(
                ok=bool(ranking_anterior),
                cpf=cpf_digitos,
                account_id=conta["Id"],
                ranking=ranking_anterior,
                mensagem=msg_r3 or err_poll or "Consulta Risk3 enviada; ranking ainda não disponível.",
                atualizacao_solicitada=True,
                atualizacao_disparada=True,
                atualizacao_erro=err_poll,
                tentativas_disparo=tentativas,
            )

        return ResultadoRanking(
            ok=bool(conta.get("Ranking__c")),
            cpf=cpf_digitos,
            account_id=conta["Id"],
            ranking=conta.get("Ranking__c"),
            mensagem=f"Não foi possível disparar IntegracaoRisk3. {msg_r3}",
            atualizacao_solicitada=True,
            atualizacao_disparada=False,
            atualizacao_erro=msg_r3,
            tentativas_disparo=tentativas,
        )

    ranking_rest, err_rest, conta_rest, tent_rest = buscar_ranking_via_rest(sf, cpf_bruto)
    if ranking_rest:
        conta_ref = conta or (buscar_conta_por_cpf(sf, cpf_bruto)[0])
        return ResultadoRanking(
            ok=True,
            cpf=cpf_digitos,
            account_id=conta_ref.get("Id") if conta_ref else None,
            ranking=ranking_rest,
            opportunity_id=opp_id,
            mensagem="Ranking encontrado.",
            tentativas_disparo=tent_rest,
        )

    if not conta and not conta_rest:
        return ResultadoRanking(
            ok=False,
            cpf=cpf_digitos,
            mensagem="Nenhuma conta encontrada para o CPF informado.",
            tentativas_disparo=tent_rest,
        )

    if conta_rest and not ranking_rest:
        return ResultadoRanking(
            ok=False,
            cpf=cpf_digitos,
            account_id=conta.get("Id") if conta else None,
            ranking=None,
            mensagem="Conta encontrada, mas sem ranking cadastrado.",
            tentativas_disparo=tent_rest,
        )

    if err_rest and "permissão" in err_rest.lower() and conta:
        return _montar_resultado(conta, cpf_digitos, opportunity_id=opp_id, tentativas_disparo=tent_rest)

    if conta:
        return _montar_resultado(
            conta,
            cpf_digitos,
            opportunity_id=opp_id,
            mensagem=err_rest or erro_soql or "Consulta concluída.",
            tentativas_disparo=tent_rest,
        )

    return ResultadoRanking(
        ok=False,
        cpf=cpf_digitos,
        mensagem=err_rest or erro_soql or "Conta não encontrada.",
        tentativas_disparo=tent_rest,
    )
