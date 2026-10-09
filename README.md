# infosiga-mp

Matrix profile ([STUMPY](https://stumpy.readthedocs.io/)) das séries de sinistros fatais, não fatais, totais e óbitos do [Infosiga SP](https://infosiga.detran.sp.gov.br/), de 2022 em diante, nas granularidades mensal, semanal e diária.

O dashboard é um HTML estático em `docs/`, servido pelo GitHub Pages e atualizado todo dia 20 pelo workflow `.github/workflows/update.yml`. Para reprocessar manualmente, rode o workflow pelo `workflow_dispatch`.

## Regras

- Sinistros: `tipo_registro` = `SINISTRO FATAL` ou `SINISTRO NAO FATAL` (notificações excluídas), pela data do sinistro.
- Óbitos: pessoas com `gravidade_lesao = FATAL`, pela data do óbito.
- Dias sem registro valem 0. A série termina no último mês completo; semanas parciais nas pontas são descartadas.
- Janelas (m): mensal 3/6/12, semanal 4/8/13, diária 7/15/30.

## Uso local

```sh
uv run pytest
uv run main.py   # baixa data/dados_infosiga.zip se não existir e gera docs/
```

Para forçar novo download, apague `data/dados_infosiga.zip`.
