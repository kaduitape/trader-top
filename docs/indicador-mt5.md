# Indicador MetaTrader — AITraderPulse

Desenha no gráfico do MetaTrader as zonas que o painel calcula: entrada,
sweet spot, take, invalidação e áreas de risco. Um botão no próprio gráfico
liga e desliga a análise da IA.

Arquivo: `scripts/mql5/AITraderPulse.mq5`
API: `GET /api/pulso` • `GET /api/pulso/status` • `POST /api/pulso/toggle`

## Isto é um Expert Advisor, não um indicador

`WebRequest()` **não pode ser chamada de um indicador**. Indicadores rodam na
thread de interface do terminal, e uma chamada de rede ali congelaria a
interface — a plataforma recusa a chamada. Um EA roda em thread própria e
pode.

Então o arquivo é um EA que se comporta como indicador: ele desenha, e só.
Não há `#include <Trade\Trade.mqh>`, não há `OrderSend`, não há `CTrade`.
Anexar ao gráfico é tão seguro quanto anexar um indicador — a diferença é
que este consegue falar com o servidor.

O mesmo vale do lado do servidor: um teste
(`test_the_route_never_touches_orders`) verifica por AST que
`app/api/routes/pulso_api.py` não importa nada de `app.execution`,
`app.paper_trading` ou `app.mt5.orders`. É garantia estrutural, não promessa.

## Instalação

**1. Gere a chave** no painel: Configurações → *Chaves de API — indicador
MetaTrader* → **Gerar chave**. Ela aparece **uma vez**; copie na hora.

Não existe "ver de novo": se o sistema conseguisse mostrar a chave depois,
um vazamento do banco também conseguiria. Perdeu, revoga e gera outra — essa
é a operação barata.

**2. Baixe o indicador** no mesmo cartão: **Baixar AITraderPulse.mq5**.

Servido pelo painel, e não pelo GitHub, porque quem instala está com o painel
aberto na frente — e porque a versão entregue ali é sempre a que conversa com
**aquele** servidor. Baixar de outro lugar abre espaço para um indicador de
uma versão e uma API de outra.

Copie para a pasta de Experts do terminal:

```text
<Pasta de Dados do MetaTrader>\MQL5\Experts\AITraderPulse.mq5
```

(No terminal: Arquivo → Abrir Pasta de Dados.) Compile no MetaEditor (F7).

O arquivo entra na imagem Docker (`COPY scripts/mql5`) e é levado à VPS pelo
deploy. Se o botão der 404, a imagem foi construída sem ele — a mensagem diz
para rodar `docker compose build app`.

**3. Libere a URL do painel** — Ferramentas → Opções → Expert Advisors →
marque *"Permitir WebRequest para as URLs listadas"* e adicione a URL exata
do painel: `https://trader-top.navit.com.br`.

A URL tem que ser **exata**, incluindo o esquema. O painel deixou de ser
alcançável por `http://<ip>:8000` — a porta 8000 agora escuta só em
`127.0.0.1` e o acesso público passa pelo Traefik com TLS.

**Sem esse passo nada funciona.** `WebRequest` devolve `-1` com erro `4014` e
o gráfico fica vazio. É a causa número um de "não funciona" — o EA detecta
esse erro e escreve a instrução no próprio gráfico.

**4. Ligue o AutoTrading** no terminal. Não porque este EA opere — ele não
opera — mas porque com o botão desligado o terminal não executa EA nenhum.

**5. Arraste o EA** para o gráfico e preencha `ApiUrl` e `ApiKey`.

## Parâmetros

| Campo | Para quê |
|---|---|
| `ApiUrl` | URL do painel, **sem barra no fim** (`https://trader-top.navit.com.br`) |
| `ApiKey` | a chave gerada no passo 1 |
| `SymbolOverride` | nome do símbolo **no painel** (vazio = o do gráfico) |
| `TimeframeOverride` | M1…MN1 (vazio = o do gráfico) |
| `TakeTicks` | take desejado — muda as zonas, não só o desenho |
| `Direction` | `AUTO`, `COMPRA` ou `VENDA` |
| `RefreshSeconds` | intervalo de consulta |
| `ZoneTransparency` | 0–100; a cor é misturada com o fundo do gráfico |
| `AlertOnReady` | avisa quando o preço entra na zona |
| `PushOnReady` | também envia push (exige MetaQuotes ID no terminal) |

### O sufixo da corretora erra em silêncio

Se a sua corretora usa `EURUSDm`, `EURUSD.raw` ou similar, o nome no gráfico
não é o nome no painel. Preencha `SymbolOverride` com o nome **do painel**.
Desenhar níveis de `EURUSD` num gráfico de `EURUSDm` põe as zonas no lugar
errado sem nenhum aviso — os números existem, só não pertencem àquele ativo.

## O botão LIGAR/DESLIGAR IA

Liga e desliga a **entrega da análise** para aquele símbolo — o que o
indicador desenha. Ele **não** para o coletor, não muda o modo do sistema e
**não envia nem cancela ordem nenhuma**. Desligar limpa a tela; não mexe numa
posição aberta.

Essa fronteira é a razão de o interruptor ser um objeto próprio no código,
e não mais um campo da automação: um botão no gráfico do MetaTrader parece
um botão de robô, e alguém vai clicar nele achando que está parando o robô.

O estado é **por símbolo** — quem opera dois ativos em duas janelas silencia
um sem apagar o outro — e fica guardado no servidor, então sobrevive a
reiniciar o terminal. O rótulo do botão diz a **ação** do clique
("DESLIGAR IA"), não o estado; e quem manda no rótulo é a resposta do
servidor, não o clique: se a chamada falhar, o botão não pode mostrar um
estado que o servidor não tem.

## A API

Autenticação por header:

```
X-API-Key: tt_...
```

Chave de API em vez do JWT do painel porque são populações opostas: sessão
de gente deve expirar rápido (30 min), credencial de máquina deve durar e ser
revogável uma a uma. Um indicador aberto no pregão não tem como refazer login.

A chave é guardada como **SHA-256**, não bcrypt. Senha de gente tem pouca
entropia e precisa de hash lento; uma chave gerada aqui tem 256 bits — força
bruta já é impossível, e o custo do bcrypt só apareceria como latência em
cada consulta.

### Por que o JSON é assim

Quem consome é MQL5, que não tem parser de JSON — o indicador extrai valor
por valor com busca de string. Isso dita o formato, e as escolhas não são
estéticas:

- **objeto raso** — cada nível de aninhamento vira mais código de parsing, e
  mais código de parsing é mais lugar para errar em silêncio;
- **nunca `null`** — ausência vira `0` mais uma flag (`has_take`, `has_stop`,
  `has_entry`). `"take": null` obrigaria o cliente a distinguir ausência de
  zero dentro de uma string;
- **`zones[]` plano**, com `kind` (`ENTRY`/`RISK`/`HEAT`) e `color`. O
  indicador percorre a lista sem conhecer a semântica, então acrescentar uma
  zona no servidor não exige recompilar o MQL5;
- **`headline` pronta** — formatar no MQL5 exigiria replicar as regras de
  arredondamento por tick, e duas formatações divergem: viraria dois preços
  diferentes para o mesmo nível.

`contract_version` é comparada pelo EA com a versão que ele entende
(`CONTRACT_SUPPORTED`). Servidor à frente do indicador vira uma mensagem no
gráfico pedindo recompilação — melhor isso do que zonas no lugar errado por
um campo que mudou de nome.

### Desligado responde 200, não erro

Com a IA desligada a rota devolve `200` com `enabled: false` e `zones: []`.
Um `4xx` faria o indicador mostrar "falha de conexão" para uma escolha do
operador — e ele precisa distinguir "você desligou" de "o servidor caiu".

### Dados velhos chegam ao indicador

`is_stale` e `data_age_minutes` vão no payload, e a `headline` vira
`DADOS DESATUALIZADOS (N min)`. Sem isso o EA desenharia zonas de ontem sobre
o preço de hoje — e o gráfico não teria como saber.

## Segurança

- A chave dá acesso a **ler análise** e ao liga/desliga do desenho. Nada além.
- Revogar tem efeito imediato: a validação consulta o banco a cada requisição.
- O segredo nunca volta pela API, nem no log de auditoria — o registro guarda
  só o prefixo (`tt_abcde…`), que identifica sem revelar.
- O painel é servido por HTTPS (Traefik, `trader-top.navit.com.br`), então a
  chave não trafega em claro. Se algum dia voltar a ser exposto por HTTP puro,
  `X-API-Key` viaja legível e quem capturar passa a ler suas análises.

## Quando não desenha nada

O EA escreve o motivo no canto do gráfico. Os quatro casos:

| No gráfico | O que fazer |
|---|---|
| `Libere <url> em Opções > Expert Advisors > WebRequest` | passo 3 da instalação |
| `Chave de API recusada` | gere outra no painel |
| `<símbolo> sem candles coletadas` | colete em Dados de mercado, ou ajuste `SymbolOverride` |
| `DADOS DESATUALIZADOS` | o coletor MT5 parou — veja Conexão MT5 |

Erro de rede **não apaga** o desenho anterior, de propósito: um gráfico que
se esvazia a cada oscilação de conexão é pior que um que mantém o último
cenário e avisa que ele envelheceu.

## Limitação conhecida

O score é **confluência**, não probabilidade de lucro — a mesma ressalva de
`docs/foto-analise.md`, e o `disclaimer` vai no payload para que ela chegue
junto com os números.

## Alerta de entrada pronta

Com `AlertOnReady`, o EA dispara `Alert()` — e `SendNotification()` se
`PushOnReady` estiver ligado — quando o status **muda** para `READY`.

Na transição, não enquanto ela dura: um aviso repetido a cada 15 segundos
vira ruído, e ruído é ignorado exatamente quando importa. Também não dispara
com dados desatualizados — dado parado não é convite para operar — nem na
primeira resposta depois de anexar o EA, que não é transição nenhuma.

## Detalhes que custaram bug

**O tick vem do painel, não do gráfico.** `g_tick_size` chega na resposta e é
o que dimensiona a área de risco e as casas decimais. Usar o `_Point` do
gráfico parece equivalente e não é: no MNQ o ponto é 0.01 e o tick é 0.25 —
a área saía 25× menor, e continuava parecendo certa.

**A headline sempre nomeia símbolo e timeframe.** Antes só o fazia quando
desligado ou desatualizado, ou seja: o caso normal, em que tudo parece
funcionar, era o único que não dizia de onde os números vinham. Com
`SymbolOverride` errado, isso é a diferença entre notar e não notar.

**Rolar o gráfico reancora as zonas sem consultar.** Os retângulos são
presos à primeira barra visível; sem tratar `CHARTEVENT_CHART_CHANGE` eles
ficavam para trás até o próximo ciclo.

**Falha de rede espaça as tentativas** (dobra até 5 min, volta ao normal no
primeiro sucesso) e **não apaga o desenho anterior**: um gráfico que se
esvazia a cada oscilação de conexão é pior que um que mantém o último
cenário e avisa que ele envelheceu.

**Durante a consulta o botão espera.** `WebRequest` é síncrona e bloqueia a
thread do EA; o clique não se perde, mas demora. O EA escreve "consultando o
painel, aguarde" em vez de parecer morto — a espera é inerente à plataforma,
não há como torná-la assíncrona.
