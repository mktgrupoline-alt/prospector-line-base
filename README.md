# Base de contas do Prospector Line

Este repositório gera, todo mês, a base de **empresas médias e grandes** usada na prospecção por contas da extensão Prospector Line.

**O que ele faz:**
1. Baixa os dados abertos do CNPJ da Receita Federal.
2. Mantém só empresas **ativas** de porte **DEMAIS** (acima de EPP).
3. Descarta as que têm capital social abaixo de R$ 100 mil **e** menos de 3 estabelecimentos.
4. Agrega por empresa e município, com a quantidade de estabelecimentos.
5. Publica um arquivo por estado (`SP.tsv.gz`, `MG.tsv.gz`...) na release **base** deste repositório.

## Como configurar (uma vez)
1. Crie uma conta gratuita no GitHub, se ainda não tiver.
2. Crie um repositório **público** chamado `prospector-line-base` e envie estes arquivos. A extensão baixa a base sem login, por isso o repositório precisa ser público. Os dados são públicos da Receita.
3. Em **Actions**, abra "Base de contas (Receita Federal)" e clique em **Run workflow** para gerar a primeira base. Leva de 1 a 3 horas, porque o servidor da Receita é lento.
4. Na extensão, abra a Prospecção por contas e preencha o repositório no formato `seu-usuario/prospector-line-base`.

Depois disso a base se atualiza sozinha todo dia 22.

## Ajustes
No arquivo `.github/workflows/base-receita.yml`:
- `MIN_CAPITAL`: capital social mínimo (padrão 100000).
- `MIN_ESTAB`: quantidade de estabelecimentos que dispensa o capital mínimo (padrão 3).

Se a Receita mudar o endereço dos arquivos, ajuste `RF_WEBDAV` e `RF_TOKEN` no script.

## Tamanho dos arquivos
Cada estado vira um arquivo compactado. São Paulo é o maior. O log do workflow mostra o tamanho de cada um ao final. Se algum passar de uns 30 MB, a busca na extensão fica lenta: aumente `MIN_CAPITAL` (por exemplo para 500000) e rode de novo.

## Radar de gatilhos
Cada linha traz quatro colunas calculadas por empresa (raiz do CNPJ), com referência na data da base:
- `unid_novas_12m` e `unid_novas_6m`: filiais **ativas** com início de atividade nos últimos 12 e 6 meses.
- `novas_recentes`: até 5 unidades novas mais recentes, no formato `codigo_municipio:AAAAMMDD`, separadas por vírgula.
- `abertura_matriz`: data de início da matriz (a extensão marca como "empresa nova" quando tem menos de 2 anos).
- `fantasias`: até 3 nomes fantasia mais usados pela empresa, somando todas as unidades ativas do Brasil, no formato `NOME:qtd|NOME:qtd` (sem número de loja). É o que permite achar a empresa pela marca (Petz, Assaí, Pão de Açúcar) quando a razão social é outra e a matriz não tem fantasia.

O `meta.json` informa a janela usada em `radar`. Matriz nunca conta como unidade nova; data futura é descartada como erro de cadastro.

Testes: `python3 -m unittest discover -s tests -v`.

## Limitações
- A Receita não informa número de funcionários. "Porte DEMAIS" quer dizer faturamento acima de R$ 4,8 milhões por ano, e junta média e grande empresa. Use a quantidade de estabelecimentos e o capital social para separar.
- O endereço dos arquivos da Receita já mudou antes. Se o workflow falhar ao listar os arquivos, confira o endereço atual do compartilhamento e ajuste `RF_WEBDAV`/`RF_TOKEN`.
