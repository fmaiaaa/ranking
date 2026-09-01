# -*- coding: utf-8 -*-
"""
Consulta e atualização de ranking de cliente no Salesforce (Account.Ranking__c).

O ranking é gravado pelo flow Consultar_Status_CPF_Serasa (ação rápida na Conta).
Via API REST o campo é somente leitura; a atualização tenta invocar a quick action
Account.Consultar_Status_CPF e aguardar o preenchimento.
"""
from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

QA_ACCOUNT = "sobjects/Account/quickActions/Consultar_Status_CPF"
QA_OPPORTUNITY = "sobjects/Opportunity/quickActions/Consultar_Status_CPFOP"

ERRO_SCREEN_FLOW_API = (
    "O flow Consultar_Status_CPF_Serasa é do tipo tela (Screen Flow) e não pode "
    "ser executado pela API REST com o usuário de integração atual. "
    "Peça ao administrador Salesforce para expor um subflow autolaunched invocável "
    "via API ou um endpoint Apex REST para consulta Serasa."
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
    """
    Tenta disparar a consulta Serasa via quick actions expostas na API.
    Retorna (disparou_sem_erro_imediato, mensagem_erro, tentativas).
    """
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
    """
    Faz polling até o ranking ou a data da última consulta mudar, ou estourar o timeout.
    """
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


def consultar_ranking(
    sf,
    cpf_bruto: str,
    *,
    forcar_atualizacao: bool = False,
    timeout_seg: float = 90.0,
    intervalo_seg: float = 5.0,
) -> ResultadoRanking:
    """
    Orquestrador principal:
    - Busca a conta pelo CPF
    - Opcionalmente tenta disparar nova consulta Serasa e aguarda atualização
    - Retorna ranking atual (ou vazio se indisponível)
    """
    conta, erro = buscar_conta_por_cpf(sf, cpf_bruto)
    cpf_digitos = normalizar_cpf(cpf_bruto)
    if erro or not conta:
        return ResultadoRanking(ok=False, cpf=cpf_digitos, mensagem=erro or "Conta não encontrada.")

    opp_id = buscar_oportunidade_recente(sf, conta["Id"])
    base = _montar_resultado(conta, cpf_digitos, opportunity_id=opp_id)

    if not forcar_atualizacao:
        return base

    base.atualizacao_solicitada = True
    disparou, err, tentativas = disparar_consulta_ranking(sf, conta["Id"], opp_id)
    base.tentativas_disparo = tentativas
    base.atualizacao_disparada = disparou
    base.atualizacao_erro = err

    if not disparou:
        base.mensagem = (
            f"{base.mensagem} Nova consulta não pôde ser disparada pela API."
        )
        if err:
            base.mensagem = f"{base.mensagem} {err}"
        return base

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
    elif timeout_seg > 0:
        resultado.mensagem = (
            "Consulta disparada, mas o ranking não foi atualizado dentro do tempo de espera. "
            "Tente novamente em instantes ou consulte manualmente no Salesforce."
        )
    return resultado


def formatar_score(score) -> str:
    if score is None:
        return "—"
    valor = float(score)
    if valor <= 1:
        valor *= 100
    return f"{valor:.0f}%"
