@RestResource(urlMapping='/update-ranking')
global with sharing class UpdateRankingRest {

    @HttpPost
    global static String updateRankingById() {
        try {
            RestRequest req = RestContext.request;
            String body = req.requestBody.toString();
            System.debug('JSON recebido: ' + body);

            RequestData input = (RequestData) JSON.deserialize(body, RequestData.class);

            if (String.isBlank(input.id_risk3) || String.isBlank(input.ranking)) {
                return 'O Id e Ranking são obrigatórios.';
            }

            List<Account> contas = [
                SELECT Id, CPF__c, Ranking__c, Id_Risk3__c
                FROM Account
                WHERE Id_Risk3__c = :input.id_risk3
                LIMIT 1
            ];

            if (contas.isEmpty()) {
                return 'Conta não encontrada para o Id informado.';
            }

            Account conta = contas[0];
            conta.Ranking__c = input.ranking;
            update conta;

            return 'Ranking atualizado com sucesso';
        } catch (Exception ex) {
            return 'Erro ao atualizar ranking: ' + ex.getMessage();
        }
    }

    @HttpGet
    global static String getRanking() {
        try {
            RestRequest req = RestContext.request;
            String idRisk3 = req.params.get('id_risk3');
            String cpf = req.params.get('cpf');
            String cnpj = req.params.get('cnpj');

            List<Account> contas = new List<Account>();

            if (!String.isBlank(idRisk3)) {
                contas = [
                    SELECT Id, CPF__c, Ranking__c
                    FROM Account
                    WHERE Id_Risk3__c = :idRisk3
                    LIMIT 1
                ];
            } else if (!String.isBlank(cpf)) {
                contas = [
                    SELECT Id, CPF__c, Ranking__c
                    FROM Account
                    WHERE CPF__c = :cpf
                    LIMIT 1
                ];
            } else if (!String.isBlank(cnpj)) {
                contas = [
                    SELECT Id, CNPJ__pc, Ranking__c
                    FROM Account
                    WHERE CNPJ__pc = :cnpj
                    LIMIT 1
                ];
            } else {
                return 'Informe um dos parâmetros: id_risk3, cpf ou cnpj.';
            }

            if (contas.isEmpty()) {
                return 'Conta não encontrada.';
            }

            Account conta = contas[0];
            return 'Ranking: ' + conta.Ranking__c;

        } catch (Exception ex) {
            return 'Erro ao buscar ranking: ' + ex.getMessage();
        }
    }

    global class RequestData {
        public String id_risk3;
        public String ranking;
    }
}
