/**
 * Consulta ranking via IntegracaoRisk3 usando CPF e regional comercial.
 * Pode usar Conta existente ou instanciar dados temporários em memória.
 */
public with sharing class ConsultorRankingRisk3 {

    public class ResultadoConsulta {
        public Boolean ok;
        public String ranking;
        public String mensagem;
        public String resultadoIntegracao;
        public Id accountId;
    }

    public static String formatarCpf(String cpf) {
        if (String.isBlank(cpf)) {
            return cpf;
        }
        String digits = cpf.replaceAll('[^0-9]', '');
        if (digits.length() != 11) {
            return cpf.trim();
        }
        return digits.substring(0, 3) + '.' +
            digits.substring(3, 6) + '.' +
            digits.substring(6, 9) + '-' +
            digits.substring(9, 11);
    }

    public static String normalizarRegional(String regionalComercial) {
        if (String.isBlank(regionalComercial)) {
            return 'RJ';
        }
        return regionalComercial.trim().toUpperCase();
    }

    public static String consultarRankingPorCpf(String cpfCliente, String regionalComercial) {
        ResultadoConsulta resultado = consultarRankingDetalhado(cpfCliente, regionalComercial);
        if (resultado.ok && String.isNotBlank(resultado.ranking)) {
            return resultado.ranking;
        }
        return String.isNotBlank(resultado.mensagem)
            ? resultado.mensagem
            : resultado.resultadoIntegracao;
    }

    public static ResultadoConsulta consultarRankingDetalhado(
        String cpfCliente,
        String regionalComercial
    ) {
        ResultadoConsulta out = new ResultadoConsulta();
        out.ok = false;

        String cpfFmt = formatarCpf(cpfCliente);
        String regional = normalizarRegional(regionalComercial);

        if (String.isBlank(cpfFmt) || cpfFmt.replaceAll('[^0-9]', '').length() != 11) {
            out.mensagem = 'CPF inválido. Informe 11 dígitos.';
            return out;
        }

        List<Account> contas = [
            SELECT Id, Name, CPF__c, Regional__c, Regional_Comercial__c, Ranking__c
            FROM Account
            WHERE CPF__c = :cpfFmt
            ORDER BY LastModifiedDate DESC
            LIMIT 1
        ];

        Account acc;
        if (!contas.isEmpty()) {
            acc = contas[0];
            if (String.isBlank(acc.Regional_Comercial__c)) {
                acc.Regional_Comercial__c = regional;
            }
            if (String.isBlank(acc.Regional__c)) {
                acc.Regional__c = regional == 'SP' ? 'SP' : regional;
            }
        } else {
            acc = new Account(
                Name = 'Cliente Consulta Temporária',
                CPF__c = cpfFmt,
                Regional__c = regional == 'SP' ? 'SP' : regional,
                Regional_Comercial__c = regional
            );
        }

        IntegracaoRisk3.ParametroEntrada parametro = new IntegracaoRisk3.ParametroEntrada();
        parametro.Account = acc;
        parametro.Opportunity = null;
        parametro.bypassRisk3 = true;

        try {
            List<IntegracaoRisk3.ResultRisk3> resultados = IntegracaoRisk3.consultarStatusCPFCNPJ(
                new List<IntegracaoRisk3.ParametroEntrada>{ parametro }
            );

            if (resultados != null && !resultados.isEmpty()) {
                out.resultadoIntegracao = resultados[0].result;
            }

            Account atualizada = buscarContaAtualizada(acc.Id, cpfFmt);
            if (atualizada != null) {
                out.accountId = atualizada.Id;
                out.ranking = atualizada.Ranking__c;
                out.ok = String.isNotBlank(atualizada.Ranking__c);
                out.mensagem = out.ok
                    ? 'Ranking encontrado.'
                    : 'Consulta Risk3 executada, mas ranking ainda não disponível.';
            } else if (String.isNotBlank(out.resultadoIntegracao)) {
                out.mensagem = out.resultadoIntegracao;
            } else {
                out.mensagem = 'Consulta Risk3 executada.';
            }
        } catch (Exception e) {
            out.mensagem = 'Erro ao consultar Risk3: ' + e.getMessage();
        }

        return out;
    }

    private static Account buscarContaAtualizada(Id accountId, String cpfFmt) {
        if (accountId != null) {
            List<Account> porId = [
                SELECT Id, Ranking__c, Ranking_Score__c, UltimaConsultaCPF__c
                FROM Account
                WHERE Id = :accountId
                LIMIT 1
            ];
            if (!porId.isEmpty()) {
                return porId[0];
            }
        }

        List<Account> porCpf = [
            SELECT Id, Ranking__c, Ranking_Score__c, UltimaConsultaCPF__c
            FROM Account
            WHERE CPF__c = :cpfFmt
            ORDER BY LastModifiedDate DESC
            LIMIT 1
        ];
        return porCpf.isEmpty() ? null : porCpf[0];
    }
}
