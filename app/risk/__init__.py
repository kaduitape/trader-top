"""Motor de risco (Fase 11): independente de estratégia, com poder de veto
sobre qualquer sinal antes que uma ordem possa ser sequer verificada,
quanto mais enviada.

Implementa em código as regras inegociáveis do prompt mestre (seção 2):
nenhum sinal sem stop-loss é aprovado; o dimensionamento de posição nunca
aumenta depois de uma perda (nada de martingale/soros — é sempre uma
fração fixa do saldo, recalculada do zero a cada sinal, nunca em função
do resultado do trade anterior); circuit breakers de 4 níveis (`WARNING`,
`SOFT_BLOCK`, `HARD_BLOCK`, `EMERGENCY_STOP`) bloqueiam novas entradas
antes que o limite vire prejuízo real."""
