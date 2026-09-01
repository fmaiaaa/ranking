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
import secrets
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

APEX_REST_UPDATE_RANKING = "update-ranking"
APEX_ACTION_INTEGRACAO_RISK3 = "actions/custom/apex/IntegracaoRisk3"
PREFIXO_CONTA_SIMULADOR = "DIRESIMULATOR"
_RECORD_TYPE_CLIENTE_PF: Optional[str] = None


def _tick_progresso(barra: Any, n: int = 1) -> None:
    if barra is not None and hasattr(barra, "update"):
        barra.update(n)


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


def gerar_token_conta_temporaria() -> str:
    return secrets.token_hex(4).upper()


def gerar_telefone_temporario() -> str:
    sufixo = secrets.randbelow(100_000_000)
    return f"(21) 9{sufixo // 10_000:04d}-{sufixo % 10_000:04d}"


def _obter_record_type_cliente_pf(sf) -> str:
    global _RECORD_TYPE_CLIENTE_PF
    if _RECORD_TYPE_CLIENTE_PF:
        return _RECORD_TYPE_CLIENTE_PF
    soql = (
        "SELECT Id FROM RecordType WHERE SObjectType = 'Account' "
        "AND DeveloperName = 'ClientePessoaFisica' LIMIT 1"
    )
    res = sf.query(soql)
    registros = res.get("records") or []
    if not registros:
        raise RuntimeError("RecordType ClientePessoaFisica não encontrado na org.")
    _RECORD_TYPE_CLIENTE_PF = registros[0]["Id"]
    return _RECORD_TYPE_CLIENTE_PF


def _query_ids(sf, soql: str) -> List[str]:
    try:
        res = sf.query(soql)
        return [rec["Id"] for rec in (res.get("records") or []) if rec.get("Id")]
    except Exception:
        return []


def _resumir_erro_exclusao(exc: Exception) -> str:
    texto = str(exc)
    baixo = texto.lower()
    if "insufficient access" in baixo:
        return "sem permissão de exclusão"
    if "delete_failed" in baixo or "não foi possível concluir" in baixo:
        return "bloqueado por dependências"
    if "cannot reference person contact" in baixo:
        return "conta pessoa — excluir apenas a Account"
    return texto[:100]


def _excluir_por_soql(sf, sobject_api: str, soql: str) -> List[str]:
    erros: List[str] = []
    objeto = getattr(sf, sobject_api.replace("-", "_"), None)
    if objeto is None:
        return erros
    for registro_id in _query_ids(sf, soql):
        try:
            objeto.delete(registro_id)
        except Exception as exc:
            erros.append(f"{sobject_api}: {_resumir_erro_exclusao(exc)}")
    return erros


def conta_e_simulador(conta: Dict[str, Any]) -> bool:
    return (conta.get("FirstName") or "").strip().upper() == PREFIXO_CONTA_SIMULADOR


def _conta_id_e_simulador(sf, account_id: str) -> bool:
    soql = (
        f"SELECT FirstName FROM Account WHERE Id = '{_escape_soql(account_id)}' LIMIT 1"
    )
    res = sf.query(soql)
    registros = res.get("records") or []
    if not registros:
        return False
    return conta_e_simulador(registros[0])


def criar_conta_temporaria(
    sf,
    cpf_bruto: str,
    *,
    regional_comercial: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Cria Account PF temporária (DIRESIMULATOR + token).
    O CPF é definido em etapa separada para contornar regra de duplicidade no insert.
    """
    cpf_digitos = normalizar_cpf(cpf_bruto)
    if len(cpf_digitos) != 11:
        raise ValueError("CPF deve conter 11 dígitos.")

    regional = (regional_comercial or regional_comercial_padrao()).strip().upper()
    token = gerar_token_conta_temporaria()
    cpf_mask = cpf_mascarado(cpf_digitos)
    ultimo_erro: Optional[Exception] = None

    for _ in range(5):
        payload = {
            "RecordTypeId": _obter_record_type_cliente_pf(sf),
            "FirstName": PREFIXO_CONTA_SIMULADOR,
            "LastName": token,
            "Regional__c": regional,
            "Regional_Comercial__c": regional,
            "TelefoneAdicional__c": gerar_telefone_temporario(),
        }
        try:
            res = sf.Account.create(payload)
            account_id = res["id"]
            try:
                sf.Account.update(account_id, {"CPF__c": cpf_mask})
            except Exception as exc:
                try:
                    sf.Account.delete(account_id)
                except Exception:
                    pass
                raise RuntimeError(
                    f"Falha ao definir CPF na conta temporária: {exc}"
                ) from exc

            return {
                "Id": account_id,
                "Name": f"{PREFIXO_CONTA_SIMULADOR} {token}",
                "FirstName": PREFIXO_CONTA_SIMULADOR,
                "LastName": token,
                "CPF__c": cpf_mask,
                "token": token,
            }
        except Exception as exc:
            ultimo_erro = exc
            if "DUPLICATES_DETECTED" not in str(exc):
                raise

    raise RuntimeError(
        f"Não foi possível criar conta temporária após várias tentativas: {ultimo_erro}"
    )


def excluir_vinculos_conta(
    sf,
    account_id: str,
    cpf_bruto: Optional[str] = None,
) -> List[str]:
    """Remove objetos filhos da conta temporária (melhor esforço)."""
    erros: List[str] = []
    aid = _escape_soql(account_id)

    erros.extend(
        _excluir_por_soql(
            sf,
            "RelacionamentoComprador__c",
            f"SELECT Id FROM RelacionamentoComprador__c WHERE Conta__c = '{aid}'",
        )
    )

    oportunidades = _query_ids(
        sf, f"SELECT Id FROM Opportunity WHERE AccountId = '{aid}'"
    )
    for opp_id in oportunidades:
        erros.extend(
            _excluir_por_soql(
                sf,
                "OpportunityContactRole",
                f"SELECT Id FROM OpportunityContactRole WHERE OpportunityId = '{_escape_soql(opp_id)}'",
            )
        )

    erros.extend(
        _excluir_por_soql(
            sf,
            "Opportunity",
            f"SELECT Id FROM Opportunity WHERE AccountId = '{aid}'",
        )
    )

    cpf_digitos = normalizar_cpf(cpf_bruto or "")
    if len(cpf_digitos) == 11:
        mascara = _escape_soql(cpf_mascarado(cpf_digitos))
        for campo in ("CPF_Consultado__c", "CPF__c"):
            erros.extend(
                _excluir_por_soql(
                    sf,
                    "Log_Risk3__c",
                    f"SELECT Id FROM Log_Risk3__c WHERE {campo} = '{mascara}'",
                )
            )

    return erros


def excluir_conta_temporaria(sf, account_id: str) -> List[str]:
    """Exclui a Account temporária somente se for DIRESIMULATOR."""
    if not _conta_id_e_simulador(sf, account_id):
        return [f"Conta {account_id} não é temporária ({PREFIXO_CONTA_SIMULADOR}); exclusão ignorada."]
    try:
        sf.Account.delete(account_id)
        return []
    except Exception as exc:
        return [f"Account: {_resumir_erro_exclusao(exc)}"]


def limpar_conta_simulador(
    sf,
    account_id: str,
    cpf_bruto: Optional[str] = None,
) -> List[str]:
    if not _conta_id_e_simulador(sf, account_id):
        return []
    erros = excluir_vinculos_conta(sf, account_id, cpf_bruto)
    erros.extend(excluir_conta_temporaria(sf, account_id))
    return erros


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


def _normalizar_texto_resposta(texto: str) -> str:
    resposta = (texto or "").strip()
    if resposta.startswith('"') and resposta.endswith('"'):
        try:
            decodificado = json.loads(resposta)
            if isinstance(decodificado, str):
                return decodificado.strip()
        except json.JSONDecodeError:
            pass
    return resposta


def _parse_resposta_update_ranking(texto: str) -> Tuple[Optional[str], Optional[str], bool]:
    """
    Retorna (ranking, erro, conta_encontrada).
    ranking None + conta_encontrada True = conta existe sem ranking.
    """
    resposta = _normalizar_texto_resposta(texto)
    if resposta.startswith("Ranking:"):
        ranking = resposta[len("Ranking:") :].strip()
        if ranking.lower() in ("", "null", "none"):
            return None, None, True
        return ranking, None, True
    if resposta == "Conta não encontrada.":
        return None, None, False
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
    barra_progresso: Any = None,
) -> Tuple[Optional[str], Optional[str]]:
    fim = time.monotonic() + timeout_seg
    ultimo_ranking: Optional[str] = None
    ultimo_erro: Optional[str] = None

    while time.monotonic() < fim:
        ranking, erro, _, _ = buscar_ranking_via_rest(sf, cpf_bruto)
        ultimo_ranking = ranking
        ultimo_erro = erro
        if ranking and ranking != ranking_anterior:
            _tick_progresso(barra_progresso)
            return ranking, None
        if ranking and not ranking_anterior:
            _tick_progresso(barra_progresso)
            return ranking, None
        _tick_progresso(barra_progresso)
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


def consultar_ranking_via_conta_temporaria(
    sf,
    cpf_bruto: str,
    *,
    regional_comercial: Optional[str] = None,
    timeout_seg: float = 90.0,
    intervalo_seg: float = 5.0,
    barra_progresso: Any = None,
) -> ResultadoRanking:
    """
    Cria conta DIRESIMULATOR, consulta Risk3, retorna ranking e remove tudo no finally.
    """
    cpf_digitos = normalizar_cpf(cpf_bruto)
    if len(cpf_digitos) != 11:
        return ResultadoRanking(
            ok=False,
            cpf=cpf_digitos,
            mensagem="Informe um CPF válido com 11 dígitos.",
        )

    account_id: Optional[str] = None
    resultado: Optional[ResultadoRanking] = None
    try:
        conta_temp = criar_conta_temporaria(
            sf,
            cpf_bruto,
            regional_comercial=regional_comercial,
        )
        _tick_progresso(barra_progresso)
        account_id = conta_temp["Id"]
        opp_id = buscar_oportunidade_recente(sf, account_id)

        disparou, msg_r3, tentativas = disparar_integracao_risk3(
            sf, account_id, opp_id, bypass=True
        )
        _tick_progresso(barra_progresso)
        ranking_poll: Optional[str] = None
        err_poll: Optional[str] = None

        if disparou:
            ranking_poll, err_poll = aguardar_ranking_via_rest(
                sf,
                cpf_bruto,
                timeout_seg=timeout_seg,
                intervalo_seg=intervalo_seg,
                barra_progresso=barra_progresso,
            )

        if not ranking_poll:
            try:
                atual = ler_ranking_conta(sf, account_id)
                ranking_poll = atual.get("Ranking__c")
            except Exception:
                pass

        if ranking_poll:
            mensagem = msg_r3 or "Ranking obtido via conta temporária DIRESIMULATOR."
        elif disparou:
            mensagem = err_poll or msg_r3 or "Consulta Risk3 enviada; ranking ainda não disponível."
        else:
            mensagem = msg_r3 or "Não foi possível disparar IntegracaoRisk3 na conta temporária."

        resultado = ResultadoRanking(
            ok=bool(ranking_poll),
            cpf=cpf_digitos,
            account_id=account_id,
            account_name=conta_temp.get("Name"),
            ranking=ranking_poll,
            opportunity_id=opp_id,
            mensagem=mensagem,
            atualizacao_solicitada=True,
            atualizacao_disparada=disparou,
            atualizacao_erro=err_poll if disparou and not ranking_poll else None,
            tentativas_disparo=tentativas,
        )
    except Exception as exc:
        resultado = ResultadoRanking(
            ok=False,
            cpf=cpf_digitos,
            account_id=account_id,
            mensagem=f"Falha na consulta via conta temporária: {exc}",
            atualizacao_solicitada=True,
        )
    finally:
        if account_id:
            _tick_progresso(barra_progresso)
            avisos = limpar_conta_simulador(sf, account_id, cpf_bruto)
            if resultado and avisos:
                resumo = "; ".join(avisos[:2])
                if len(avisos) > 2:
                    resumo += f" (+{len(avisos) - 2} avisos)"
                resultado.mensagem = f"{resultado.mensagem} Limpeza parcial: {resumo}".strip()

    return resultado or ResultadoRanking(
        ok=False,
        cpf=cpf_digitos,
        mensagem="Falha inesperada na consulta via conta temporária.",
    )


def _montar_resultado(conta: Dict[str, Any], cpf_digitos: str, **extra) -> ResultadoRanking:
    ranking = conta.get("Ranking__c")
    mensagem = extra.pop("mensagem", None)
    if not mensagem:
        mensagem = "Ranking encontrado." if ranking else "Conta encontrada, mas sem ranking cadastrado."
    return ResultadoRanking(
        ok=bool(ranking),
        cpf=cpf_digitos,
        account_id=conta.get("Id"),
        account_name=conta.get("Name"),
        ranking=ranking,
        ranking_score=conta.get("Ranking_Score__c"),
        ultima_consulta_cpf=conta.get("UltimaConsultaCPF__c"),
        mensagem=mensagem,
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
    regional_comercial: Optional[str] = None,
    timeout_seg: float = 90.0,
    intervalo_seg: float = 5.0,
    barra_progresso: Any = None,
) -> ResultadoRanking:
    """
    Fluxo principal:
    1. Localiza Account por CPF (SOQL interno).
    2. Sem conta → DIRESIMULATOR + Risk3 + polling REST.
    3. Com conta → Risk3 + polling REST; SOQL na Account só como fallback.
    """
    regional = regional_comercial or regional_comercial_padrao()
    cpf_digitos = normalizar_cpf(cpf_bruto)
    if len(cpf_digitos) != 11:
        return ResultadoRanking(ok=False, cpf=cpf_digitos, mensagem="Informe um CPF válido com 11 dígitos.")

    conta, erro_soql = buscar_conta_por_cpf(sf, cpf_bruto)
    _tick_progresso(barra_progresso)
    if not conta:
        return consultar_ranking_via_conta_temporaria(
            sf,
            cpf_bruto,
            regional_comercial=regional,
            timeout_seg=timeout_seg,
            intervalo_seg=intervalo_seg,
            barra_progresso=barra_progresso,
        )

    opp_id = buscar_oportunidade_recente(sf, conta["Id"])
    ranking_anterior = conta.get("Ranking__c")

    disparou, msg_r3, tentativas = disparar_integracao_risk3(sf, conta["Id"], opp_id, bypass=True)
    _tick_progresso(barra_progresso)

    ranking_poll: Optional[str] = None
    err_poll: Optional[str] = None

    if disparou:
        ranking_poll, err_poll = aguardar_ranking_via_rest(
            sf,
            cpf_bruto,
            timeout_seg=timeout_seg,
            intervalo_seg=intervalo_seg,
            ranking_anterior=ranking_anterior,
            barra_progresso=barra_progresso,
        )

    if not ranking_poll:
        try:
            atual = ler_ranking_conta(sf, conta["Id"])
            ranking_poll = atual.get("Ranking__c")
        except Exception:
            ranking_poll = ranking_anterior
        _tick_progresso(barra_progresso)

    if ranking_poll:
        mensagem = (
            msg_r3 or "Ranking atualizado via IntegracaoRisk3."
            if disparou
            else "Ranking encontrado via SOQL (fallback)."
        )
        return ResultadoRanking(
            ok=True,
            cpf=cpf_digitos,
            account_id=conta["Id"],
            ranking=ranking_poll,
            opportunity_id=opp_id,
            mensagem=mensagem,
            atualizacao_solicitada=True,
            atualizacao_disparada=disparou,
            tentativas_disparo=tentativas,
        )

    return ResultadoRanking(
        ok=False,
        cpf=cpf_digitos,
        account_id=conta["Id"],
        ranking=None,
        mensagem=msg_r3 or err_poll or erro_soql or "Ranking não disponível após consulta Risk3.",
        atualizacao_solicitada=True,
        atualizacao_disparada=disparou,
        atualizacao_erro=err_poll,
        tentativas_disparo=tentativas,
    )
