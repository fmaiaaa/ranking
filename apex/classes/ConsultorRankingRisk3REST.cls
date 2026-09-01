/**
 * Endpoint REST para consulta de ranking por CPF via IntegracaoRisk3.
 * POST /services/apexrest/consulta-ranking/v1
 * Body: {"cpf":"12345678901","regionalComercial":"RJ"}
 */
@RestResource(urlMapping='/consulta-ranking/v1/*')
global with sharing class ConsultorRankingRisk3REST {

    global class RequestBody {
        global String cpf;
        global String regionalComercial;
    }

    global class ResponseBody {
        global Boolean ok;
        global String ranking;
        global String mensagem;
        global String resultadoIntegracao;
        global String accountId;
    }

    @HttpPost
    global static ResponseBody consultar() {
        RestRequest req = RestContext.request;
        RequestBody body;

        if (req.requestBody != null && req.requestBody.size() > 0) {
            body = (RequestBody) JSON.deserialize(req.requestBody.toString(), RequestBody.class);
        } else {
            body = new RequestBody();
            body.cpf = req.params.get('cpf');
            body.regionalComercial = req.params.get('regionalComercial');
        }

        ConsultorRankingRisk3.ResultadoConsulta resultado =
            ConsultorRankingRisk3.consultarRankingDetalhado(body.cpf, body.regionalComercial);

        ResponseBody resp = new ResponseBody();
        resp.ok = resultado.ok;
        resp.ranking = resultado.ranking;
        resp.mensagem = resultado.mensagem;
        resp.resultadoIntegracao = resultado.resultadoIntegracao;
        resp.accountId = resultado.accountId != null ? String.valueOf(resultado.accountId) : null;
        return resp;
    }
}
