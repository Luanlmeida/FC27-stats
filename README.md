# FC 27 Stats — Arsenálcøøl FC

Painel de estatísticas do Pro Clubs (clube ID 749668).

https://fc27-stats-arsenalcool.onrender.com/medalhas

## Estrutura
- app.py: Flask, consultas à API, cálculos e rotas
- templates/painel.html: interface e gráficos (Chart.js)
- /debug: JSON bruto retornado pela API (para conferir os campos reais)

## Observações
- Para outro clube: `set CLUBE_ID=123456` antes de rodar.
- A API é não oficial: campos ausentes aparecem como 0 ou "—".
- Os dados ficam em cache por 60 segundos.
- Datas em UTC; o radar e os destaques são calculados a partir dos dados.
