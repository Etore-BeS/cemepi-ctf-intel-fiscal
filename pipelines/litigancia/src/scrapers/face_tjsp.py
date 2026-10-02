import re
import time

import certifi
import pandas as pd
import requests
from bs4 import BeautifulSoup
from requests.exceptions import RequestException


class FaceTJSPScraper:
    def __init__(self, *, request_timeout: float = 15.0, max_request_retries: int = 2):
        self.request_timeout = request_timeout
        self.max_request_retries = max_request_retries
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
        )
        self._ca_bundle = certifi.where()

        self.padrão_autores = [
            "Requerente:",
            "Requerente",
            "Reqte:",
            "Reqte",
            "Autor:",
            "Autor",
            "Autora:",
            "Autora",
            "Exequente:",
            "Exequente",
            "Exeqte:",
            "Exeqte",
            "Exeqte&nbsp;",
            "Reclamante:",
            "Reclamante",
            "Reclte",
            "Falida:",
            "Falida",
            "Falido:",
            "Falido",
            "Embargante:",
            "Embargante",
            "Embargte:",
            "Embargte",
            "Agravte",
            "Invtante",
            "LiqdteAt",
            "Alimentada",
            "Apte/Reqdo",
            "Imptte",
        ]
        self.padrão_réus = [
            "Requerido:",
            "Requerido",
            "Requerida:",
            "Requerida",
            "Reqdo:",
            "Reqdo",
            "Reqda:",
            "Reqda",
            "Réu",
            "Réu:",
            "Ré",
            "Ré:",
            "Executado:",
            "Executado",
            "Executada:",
            "Executada",
            "Exectdo",
            "Exectdo:",
            "Exectda:",
            "Exectda",
            "Reclamado:",
            "Reclamado",
            "Recldo:",
            "Recldo",
            "Reclda:",
            "Reclda",
            "Credor:",
            "Credor",
            "Credora",
            "Credora:",
            "Embargado:",
            "Embargado",
            "Embargdo:",
            "Embargdo",
            "Embargda:",
            "Embargda",
            "Agravdo",
            "LiqdtePas",
            "Alimentante",
            "Rqrte/Qrldo",
            "Imptdo",
        ]
        self.padrão_sentenças = [
            "Decretada a Falência",
            "Sentença de Revelia",
            "Homologada Renúncia pelo Autor",
            "Julgamento/Homologação de Partilha ou Adjudicação",
            "Declarado o Encerramento da Falência",
            "Decretado o Encerramento da Recuperação Judicial",
            "Julgada Improcedente a Ação",
            "Julgado Improcedente o Pedido e o Pedido Contraposto",
            "Julgado Improcedente o Pedido e Procedente o Pedido Contraposto",
            "Julgado Improcedente o Pedido e Procedente em Parte o Pedido Contraposto",
            "Julgado Improcedente o Pedido e a Reconvenção",
            "Julgado Improcedente o Pedido e Procedente a Reconvenção",
            "Julgado Improcedente o Pedido e Procedente em Parte a Reconvenção",
            "Julgada Improcedente a Ação - Art. 332, do CPC",
            "Julgados Improcedentes os Embargos à Execução",
            "Julgada Improcedente a Reabilitação",
            "Julgada Procedente a Ação",
            "Julgado Procedente o Pedido - Reconhecimento pelo Réu",
            "Julgado Procedente o Pedido e Procedência do Pedido Contraposto",
            "Julgado Procedente o Pedido e Procedente em Parte do Pedido Contraposto",
            "Julgado Procedente o Pedido e Improcedente o Pedido Contraposto",
            "Julgado Procedente o Pedido e Procedência da Reconvenção",
            "Julgado Procedente o Pedido e Procedência em Parte da Reconvenção",
            "Julgado Procedente o Pedido e Improcedência da Reconvenção",
            "Julgada Procedente a Ação com Aplicação de Multa",
            "Julgados Procedentes os Embargos à Execução",
            "Julgada Procedente em Parte a Ação",
            "Julgado Procedente em Parte o Pedido e Improcedente o Pedido Contraposto",
            "Julgado Procedente em Parte o Pedido e Procedente o Pedido Contraposto",
            "Julgado Procedente em Parte o Pedido e Procedente em Parte do Pedido Contraposto",
            "Julgado Procedente em Parte o Pedido e Improcedente a Reconvenção",
            "Julgado Procedente em Parte o Pedido e Procedência da Reconvenção",
            "Julgado Procedente em Parte o Pedido e Procedência em Parte da Reconvenção",
            "Julgados Procedentes em Parte os Embargos à Execução",
            "Declarada Decadência ou Prescrição",
            "Não Homologado o Pedido",
            "Pedido Conhecido em Parte e Improcedente",
            "Pedido Conhecido em Parte e Procedente",
            "Pedido Conhecido em Parte e Procedente em Parte",
            "Homologado o Acordo em Execução ou em Cumprimento de Sentença",
            "Homologado o Pedido",
            "Julgada Improcedente a Impugnação à Execução",
            "Julgada Procedente a Impugnação à Execução",
            "Julgada Parcialmente Procedente a Impugnação à Execução",
            "Concedido o Habeas Corpus",
            "Concedido o Habeas Corpus de Ofício",
            "Concedido o Habeas Data",
            "Concedida a Segurança",
            "Concedida a Recuperação Judicial",
            "Concedido em Parte o Habeas Corpus",
            "Concedido em Parte o Habeas Data",
            "Concedida em Parte a Segurança",
            "Denegado o Habeas Corpus",
            "Denegado o Habeas Data",
            "Denegada a Segurança",
            "Extinta a Execução/Cumprimento da Sentença pela Satisfação da Obrigação",
            "Extinta a Execução/Cumprimento da Sentença pela Remissão da Dívida, obtida p/ Transação ou Outro Meio",
            "Extinta a Execução/Cumprimento da Sentença pela Renúncia ao Crédito pelo Credor",
            "Extinta a Execução/Cumprimento da Sentença Pelo Acolhimento da Exceção de Pré-executividade",
            "Extinta a Execução pela Prescrição Intercorrente - Artigo 924, V CPC - Sem Advogado",
            "Homologada a Transação de Acordo ExtraJudicial",
            "Homologada a Transação de Acordos Obtidos por Conciliadores",
            "Homologada a Transação de Acordo por Juiz, em Audiência",
            "Homologada a Transação de Acordo Obtido em Câmara Privada",
            "Falência não Decretada por Depósito Elisivo",
            "Falência não Decretada por Improcedência do Pedido",
            "Não Conhecido o Habeas Corpus",
            "Prejudicada a Ação",
            "Extinto o Processo por Devedor não Encontrado",
            "Extinto o Processo por Inexistência de Bens Penhoráveis",
            "Extinto o Processo por Ausência do Autor à Audiência",
            "Extinto o Processo por Inadmissibilidade do Procedimento Sumaríssimo",
            "Extinto o Processo por Incompetência Territorial",
            "Extinto o Processo por Incompetência em Razão da Pessoa",
            "Extinto o Processo por Falecimento do Autor sem Habilitação de Sucessores",
            "Extinto o Processo por Ausência de Citação de Sucessores do Réu Falecido",
            "Extintos os Embargos à Execução sem Resolução do Mérito",
            "Extinto o Processo sem Resolução do Mérito por Abandono da Causa pelo Autor",
            "Extinto o Processo sem Resolução do Mérito por ser a Ação Intransmissível",
            "Extinto o Processo sem Resolução do Mérito por Convenção de Arbitragem",
            "Extinto o Processo sem Resolução do Mérito por Desistência",
            "Extinto o Processo por Negligência das Partes sem Resolução do Mérito",
            "Extinto o Processo sem Resolução do Mérito por Outras Hipóteses (Art. 485, X)",
            "Extinto o Processo com Estabilização da Tutela de Urgência (Art. 304, § 1º, do CPC)",
            "Extinto o Processo sem Resolução do Mérito por Ausência das Condições da Ação",
            "Extinto o Processo sem Resolução do Mérito por Ausência de Pressupostos Processuais",
            "Extinto o Processo sem Resolução de Mérito por Continência",
            "Indeferida a Petição Inicial sem Resolução do Mérito",
            "Extinto o Processo sem Resolução do Mérito por Perempção, Litispendência ou Coisa Julgada",
            "Autos Extintos por Renúncia",
            "Extintos os Autos em Razão de Perda de Objeto",
            "Extinção por Ausência de Requerimento Administrativo Prévio",
            "Extinto o Processo sem Resolução do Mérito por Desistência - Execução Fiscal – Artigo 485, VIII CPC – Sem Advogado",
            "Extinto o Processo sem Resolução do Mérito por Desistência - Execução Fiscal – Artigo 485, VIII CPC – Com Advogado",
            "Extinto o Processo pelo Cancelamento da Dívida Ativa",
            "Extinta a Punibilidade por Anistia, Graça ou Indulto",
            "Extinta a Punibilidade por Cumprimento da Suspensão Condicional do Processo",
            "Extinta a Punibilidade por Cumprimento da Transação Penal",
            "Extinta a Punibilidade por Decadência ou Perempção",
            "Extinta a Punibilidade por Morte do Agente",
            "Extinta a Punibilidade por Pagamento Integral do Débito",
            "Extinta a Punibilidade por Perdão Judicial",
            "Extinta a Punibilidade por Prescrição",
            "Extinta a Punibilidade por Renúncia do Queixoso ou Perdão Aceito",
            "Extinta a Punibilidade por Retratação do Agente",
            "Extinta a Punibilidade por Retroatividade de Lei",
            "Extinta a Punibilidade por Composição Civil dos Danos",
            "Extinta a Punibilidade em Razão do Cumprimento de Acordo de Não Persecução Penal",
            "Condenação à Pena de Multa Isoladamente",
            "Condenação à Pena Privativa de Liberdade SEM Decretação da prisão",
            "Condenação à Pena Privativa de Liberdade COM Decretação da Prisão",
            "Condenação à Pena Privativa de Liberdade e Multa SEM Decretação da Prisão",
            "Condenação à Pena Privativa de Liberdade e Multa COM Decretação da Prisão",
            "Condenação à Pena Privativa de Liberdade com Suspensão Condicional - SURSIS",
            "Condenação à Pena Privativa de Liberdade Substituída por Restritiva de Direito",
            "Condenação à Pena Restritiva de Direitos - Interdição Temporária de Direitos",
            "Condenação à Pena Restritiva de Direitos - Limitação de Fim de Semana",
            "Condenação à Pena Restritiva de Direitos - Perda de Bens e Valores",
            "Condenação à Pena Restritiva de Direitos - Prestação de Serviços à Comunidade",
            "Condenação à Pena Restritiva de Direitos - Prestação Pecuniária",
            "Sentença de Absolvição - Provada a Inexistência do Fato (Art. 386, I, CPP)",
            "Sentença de Absolvição - Não Haver Prova da Existência do Fato (Art. 386, II, CPP)",
            "Sentença de Absolvição - Não Constituir o Fato Infração Penal (Art. 386, III, CPP)",
            "Sentença de Absolvição - Provado que o Réu Não Concorreu para a Infração Penal (Art. 386, IV, CPP)",
            "Sentença de Absolvição - Não Existir Prova de ter o Réu Concorrido para a Infração Penal (Art. 386, V, CPP)",
            "Sentença de Absolvição - Existirem Circ. Excluam o Crime/Isentem de Pena/Dúvida sobre sua Existência (Art. 386, VI, CPP)",
            "Sentença de Absolvição - Não existir prova suficiente para condenação (Art. 386, VII, CPP)",
            "Absolvição - Imposição de Medida de Segurança - Internação",
            "Absolvição - Imposição de Medida de Segurança - Tratamento Ambulatorial",
            "Sentença de Pronúncia Sem Decretação de Prisão - Sentença Completa",
            "Sentença de Pronúncia Com Decretação de Prisão - Sentença Completa",
            "Extinta a Execução pela Prescrição Intercorrente - Artigo 924, V CPC - Com Advogado",
            "Julgada improcedente a ação",
            "Extinta a Execução pela Prescrição Intercorrente - Artigo 924, V CPC - Sem Advogado",
            "Sentença de Absolvição - Não existir prova suficiente para condenação (Art. 386, VII, CPP)",
            "Sentença Condenatória/Absolutória Proferida",
            "Sentença de Absolvição - Não Existir Prova de ter o Réu Concorrido para a Infração Penal (Art. 386, V, CPP)",
            "Proferidas Outras Decisões não Especificadas",
            "Remetido ao DJE para Republicação",
            "Julgados Improcedentes os Embargos à Adjudicação/Alienação/Arrematação",
            "Homologado o Cálculo",
            "Recuperação judicial",
            "Decisão de Saneamento e de Organização do Processo",
            "Extinto o Processo sem Resolução do Mérito por Outras Hipóteses (Art. 485, X)",
            "Julgado Improcedentes o Pedido e Procedente em Parte a Reconvenção",
            "Pedido de Homologação de Acordo Juntado",
            "Acolhida em parte a impugnação ao cumprimento de sentença",
            "Extinta a punibilidade por cumprimento da transação penal",
            "Concedida em parte a Segurança",
        ]

    def get_html(self, url):
        last_error: Exception | None = None
        for attempt in range(self.max_request_retries + 1):
            try:
                response = self.session.get(
                    url,
                    timeout=self.request_timeout,
                    verify=self._ca_bundle,
                )
                response.raise_for_status()
                return response.text
            except RequestException as e:
                last_error = e
                if attempt < self.max_request_retries:
                    time.sleep(0.5 * (attempt + 1))
                    continue
        print(f"Erro ao acessar {url}: {last_error}")
        return None

    def parse_process(self, html):
        if not html:
            return None

        soup = BeautifulSoup(html, "html.parser")

        def find_text(id_):
            element = soup.find(id=id_)
            if not element:
                # Tentativa para campos unj
                if id_ == "classeProcesso":
                    element = soup.find(class_="unj-larger")
            return element.get_text().strip() if element else "NÃO HÁ REGISTRO"

        dados = {
            "numero": find_text("numeroProcesso"),
            "classe": find_text("classeProcesso"),
            "assunto": find_text("assuntoProcesso"),
            "foro": find_text("foroProcesso"),
            "vara": find_text("varaProcesso"),
            "distribuicao": find_text("dataHoraDistribuicaoProcesso"),
            "controle": find_text("numeroControleProcesso"),
            "area": find_text("areaProcesso"),
            "valor": find_text("valorAcaoProcesso"),
        }

        # Outros assuntos
        try:
            divs = soup.find_all("div", class_="line-clamp__2")
            dados["outros_assuntos"] = divs[-1].find("span").get_text().strip()
        except Exception:
            dados["outros_assuntos"] = "NÃO HÁ REGISTRO"

        # Partes do processo
        autores, advogados_autores = [], []
        réus, advogados_réus = [], []

        tabela_partes = soup.find("table", id="tableTodasPartes") or soup.find(
            "table", id="tablePartesPrincipais"
        )
        if tabela_partes:
            linhas = tabela_partes.find_all("tr", class_="fundoClaro")
            for linha in linhas:
                texto_linha = linha.get_text()

                # Checar Autores
                is_autor = any(
                    re.search(pad, texto_linha) for pad in self.padrão_autores
                )
                if is_autor:
                    try:
                        nome = (
                            linha.find("td", class_="nomeParteEAdvogado")
                            .get_text()
                            .strip()
                            .split("\n")[0]
                        )
                        autores.append(nome)
                        # Advogados do autor
                        advs = linha.find_all("span", string=re.compile("Advogad[ao]:"))
                        for adv in advs:
                            autores_adv = (
                                adv.next_sibling.strip() if adv.next_sibling else ""
                            )
                            if autores_adv:
                                advogados_autores.append(autores_adv)
                    except Exception:
                        pass

                # Checar Réus
                is_reu = any(re.search(pad, texto_linha) for pad in self.padrão_réus)
                if is_reu:
                    try:
                        nome = (
                            linha.find("td", class_="nomeParteEAdvogado")
                            .get_text()
                            .strip()
                            .split("\n")[0]
                        )
                        réus.append(nome)
                        # Advogados do réu
                        advs = linha.find_all("span", string=re.compile("Advogad[ao]:"))
                        for adv in advs:
                            reus_adv = (
                                adv.next_sibling.strip() if adv.next_sibling else ""
                            )
                            if reus_adv:
                                advogados_réus.append(reus_adv)
                    except Exception:
                        pass

        dados["autores"] = ", ".join(autores) if autores else "NÃO HÁ REGISTRO"
        dados["advogados_autores"] = (
            ", ".join(advogados_autores) if advogados_autores else "NÃO HÁ REGISTRO"
        )
        dados["reus"] = ", ".join(réus) if réus else "NÃO HÁ REGISTRO"
        dados["advogados_reus"] = (
            ", ".join(advogados_réus) if advogados_réus else "NÃO HÁ REGISTRO"
        )

        # Movimentações e Sentenças
        dados["movimentações"] = []
        tipo_sentença, data_sentença = [], []
        tabela_movs = soup.find("tbody", id="tabelaTodasMovimentacoes")
        if tabela_movs:
            for mov in tabela_movs.find_all("tr"):
                tds = mov.find_all("td")
                if len(tds) >= 3:
                    data_mov = tds[0].get_text().strip()
                    tipo_mov = tds[2].get_text().strip()
                    for pad in self.padrão_sentenças:
                        if re.search(pad, tipo_mov, re.IGNORECASE):
                            tipo_sentença.append(tipo_mov.split("\n")[0])
                            data_sentença.append(data_mov)

                    dados["movimentações"].append({"data": data_mov, "tipo": tipo_mov})

        dados["tipo_sentença"] = tipo_sentença if tipo_sentença else [None]
        dados["data_sentença"] = data_sentença if data_sentença else [None]

        return dados


# Exemplo de uso
if __name__ == "__main__":
    lawsuit_codes = ["G80001UQ70000", "1Z0007K9K0000"]

    urls = []

    for number in lawsuit_codes:
        urls.append(f"https://esaj.tjsp.jus.br/cpopg/show.do?processo.codigo={number}")

    scraper = FaceTJSPScraper()
    lista_df = []

    for url in urls:
        print(f"Coletando: {url}")
        html = scraper.get_html(url)
        dados = scraper.parse_process(html)

        if dados:
            lista_df.append(
                (
                    url,
                    dados["numero"],
                    dados["classe"],
                    dados["assunto"],
                    dados["outros_assuntos"],
                    dados["distribuicao"],
                    dados["area"],
                    dados["valor"],
                    dados["foro"],
                    dados["vara"],
                    dados["autores"],
                    dados["advogados_autores"],
                    dados["reus"],
                    dados["advogados_reus"],
                    dados["tipo_sentença"],
                    dados["data_sentença"],
                    dados["movimentações"],
                )
            )
        else:
            lista_df.append((url, *["ERRO"] * 15))

        time.sleep(1)  # Delay educado

    df = pd.DataFrame(
        lista_df,
        columns=[
            "URL",
            "F2_Processo",
            "F2_Classe",
            "F2_Assunto",
            "Outros Assuntos",
            "Data Distribuição",
            "Área",
            "Valor Da Ação",
            "F2_Foro",
            "F2_Vara",
            "Autores",
            "Advogados Autores",
            "Réu",
            "Advogados Réus",
            "Tipo de Sentença",
            "F2_Data Sentença",
            "Movimentações",
        ],
    )

    print("Concluído. Planilha 'Dados_Detalhados_Requests.xlsx' gerada.")
    df.to_csv("Dados_Detalhados_Requests.csv", index=False)
