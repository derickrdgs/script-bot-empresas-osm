# Script Bot de Empresas OSM

Aplicação desktop em Python para pesquisar estabelecimentos e empresas mapeados no [OpenStreetMap](https://www.openstreetmap.org/), usando a API Overpass, e exportar os resultados para CSV. O projeto também oferece uma etapa opcional de enriquecimento de dados cadastrais por CNPJ.

## Funcionalidades

- Pesquisa por cidade ou região, com geocodificação pelo Nominatim.
- Definição do raio de busca em quilômetros (padrão: 60 km; máximo: 300 km).
- Filtro por categoria, como restaurantes, farmácias, oficinas, padarias, supermercados, hotéis e outras categorias mapeadas no OpenStreetMap.
- Opção para pesquisar todas as categorias compatíveis.
- Interface gráfica com Tkinter, botões para iniciar e interromper a execução e registro de status em tempo real.
- Limite de até 1.000 estabelecimentos por execução.
- Coleta de nome, categoria, endereço, telefone e coordenadas, quando esses dados estiverem disponíveis no OpenStreetMap.
- Enriquecimento opcional com CNPJ, razão social, situação cadastral e CNAE principal.
- Exportação para CSV compatível com Excel e LibreOffice em português (UTF-8 com BOM e separador ponto e vírgula).
- Salvamento dos resultados parciais quando a execução é interrompida ou ocorre uma falha.

## Como funciona

1. O Nominatim procura a cidade ou região informada e retorna um ponto central.
2. A aplicação monta uma consulta Overpass QL com o raio e a categoria selecionados.
3. A Overpass API retorna os elementos do OpenStreetMap que possuem nome.
4. O programa organiza os dados encontrados e, se habilitado, tenta complementar as informações cadastrais.
5. Ao final, gera um arquivo CSV na mesma pasta do script.

### Enriquecimento de CNPJ

A aplicação primeiro verifica se o próprio elemento do OpenStreetMap possui uma tag de CNPJ válida. Quando não encontra, tenta localizar uma empresa pelo nome e município usando um endpoint público da Casa dos Dados. A correspondência só é considerada quando a similaridade entre os nomes é de pelo menos 85%. Em seguida, consulta a BrasilAPI para obter informações cadastrais complementares.

Essa etapa é opcional e pode ser desativada na interface. Os serviços externos podem ficar indisponíveis, alterar seus endpoints ou retornar informações incompletas. Uma correspondência por nome não garante que o CNPJ pertença ao estabelecimento pesquisado; confira os dados antes de utilizá-los.

## Requisitos

- Python 3.
- Tkinter, normalmente incluído na instalação padrão do Python.
- Biblioteca externa `requests`.

## Instalação

Clone o repositório:

```bash
git clone https://github.com/derickrdgs/script-bot-empresas-osm.git
cd script-bot-empresas-osm
```

Crie e ative um ambiente virtual (recomendado):

**Windows (PowerShell):**

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

**Linux:**

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Instale a dependência:

```bash
pip install -r requirements.txt
```

## Configuração do User-Agent

Antes de executar, abra `bot_empresas_osm.py` e altere a constante `USER_AGENT`, substituindo `seu-email@exemplo.com` por um endereço de contato válido:

```python
USER_AGENT = "BotEmpresasOSM/1.0 (uso pessoal; contato: derickrdgs@gmail.com)"
```

O contato identificável é importante para o uso responsável dos serviços de geocodificação e consulta. Não execute várias instâncias simultaneamente nem faça requisições em volume que sobrecarregue os serviços públicos.

## Execução

```bash
python bot_empresas_osm.py
```

A janela abrirá com os seguintes campos:

| Campo | Descrição |
|---|---|
| Cidade ou região | Localidade usada como centro da pesquisa. |
| Raio (km) | Distância da busca, entre 1 e 300 km. |
| Categoria | Tipo de estabelecimento ou `todos`. |
| Enriquecer com CNPJ | Ativa ou desativa a tentativa de complementação cadastral. |

Clique em **INICIAR BOT** para começar. O botão **PARAR BOT** interrompe o processo de forma controlada e tenta exportar os registros já coletados.

## Arquivo CSV gerado

O arquivo é salvo na mesma pasta do script, com nome semelhante a:

```
empresas_ribeirao_preto_20261104_153000.csv
```

As colunas exportadas são:

- Nome da Empresa
- Razão Social
- CNPJ
- Categoria
- Endereço Completo
- Telefone
- Latitude
- Longitude
- Situação CNPJ
- CNAE Principal

Campos que não estiverem disponíveis são preenchidos com `Não informado`. A presença de um estabelecimento no resultado depende da cobertura e da qualidade dos dados cadastrados no OpenStreetMap.

## Limitações e uso responsável

- O projeto não consulta um cadastro completo de todas as empresas do Brasil. Ele pesquisa estabelecimentos mapeados no OpenStreetMap.
- Endereços, telefones e nomes podem estar ausentes ou desatualizados.
- O enriquecimento por CNPJ depende de serviços de terceiros e pode falhar ou retornar dados que exigem conferência.
- A etapa de enriquecimento faz várias consultas e pode ser demorada. Para uma coleta mais rápida, desmarque essa opção.
- As APIs públicas possuem políticas de uso, limites e disponibilidade próprios. Consulte a documentação antes de executar pesquisas frequentes ou em larga escala.
- Utilize os dados respeitando a legislação aplicável, os termos dos serviços consultados e a finalidade da coleta.

## Fontes e atribuição

Este projeto utiliza serviços e dados de terceiros:

- [OpenStreetMap](https://www.openstreetmap.org/) — dados geográficos sob a licença [ODbL](https://opendatacommons.org/licenses/odbl/).
- [Nominatim](https://nominatim.org/) — serviço de geocodificação.
- [Overpass API](https://overpass-api.de/) — consulta a dados do OpenStreetMap.
- [BrasilAPI](https://brasilapi.com.br/) — consulta de dados de CNPJ.
- [Casa dos Dados](https://casadosdados.com.br/) — fonte utilizada na busca pública por nome empresarial.

Ao utilizar ou redistribuir dados do OpenStreetMap, mantenha a atribuição adequada: **© OpenStreetMap contributors**, conforme os termos aplicáveis da ODbL. A licença MIT deste repositório se aplica ao código do projeto, não substitui nem altera as licenças dos dados e serviços de terceiros.

## Licença

Distribuído sob a licença MIT. Consulte o arquivo [LICENSE](LICENSE) para ver o texto completo.
