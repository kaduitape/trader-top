# Corretoras de execução — MT5 e cTrader

O sistema decide **onde** entrar; a corretora é apenas quem executa. Essa
separação é a porta `BrokerPort` (`app/broker/port.py`), e é o que permite
trocar de corretora sem tocar no motor de decisão.

## O contrato

Quatro operações. São mesmo só quatro:

| Operação | Para quê |
|---|---|
| `account()` | Saldo, moeda e — crucialmente — **se a conta é demo ou real** |
| `open_positions(symbol)` | O que está aberto agora |
| `send_market_order(request)` | Ordem a mercado **com stop e alvo anexados** |
| `modify_protection(id, sl, tp)` | Trailing e break-even |

Duas ausências deliberadas:

- **Não existe "fechar posição".** Quem encerra é o stop ou o alvo que
  viajaram anexados na ordem, do lado do broker. Abrir esse caminho aqui
  criaria por acidente a porta que o projeto inteiro evita.
- **Não existe dado de mercado.** Candles e ticks continuam vindo do
  conector MT5. Misturar as duas coisas transformaria a porta num espelho de
  plataforma de novo.

**Volume é sempre em lotes** na porta. Cada adaptador converte para a unidade
nativa da sua corretora — e essa conversão é responsabilidade exclusiva do
adaptador.

## Escolher a corretora

```ini
BROKER=mt5        # padrão
# ou
BROKER=ctrader
CTRADER_CLIENT_ID=...
CTRADER_CLIENT_SECRET=...
CTRADER_ACCESS_TOKEN=...
CTRADER_ACCOUNT_ID=12345678
CTRADER_ACCOUNT_IS_DEMO=true
```

Faltando qualquer credencial, o sistema **falha alto** e diz exatamente quais
variáveis faltam. Ele nunca volta para o MT5 em silêncio: "achei que estava
operando na cTrader" é um jeito ruim de descobrir onde o dinheiro foi parar.

## A guarda que vale nos dois caminhos

Modo configurado e tipo de conta precisam bater, **nos dois sentidos**:

- DEMO configurado com conta REAL → recusado
- REAL configurado com conta demo → recusado

O segundo caso parece inofensivo e não é: significa que o operador acredita
estar arriscando dinheiro e não está — ou o contrário.

Na cTrader há um detalhe a mais. A `ProtoOATraderRes` nem sempre traz
`isLive`. Quando não traz, o sistema usa `CTRADER_ACCOUNT_IS_DEMO`; se nem
isso estiver configurado, **recusa operar**. Chutar aqui é chutar se o
dinheiro é de verdade.

## Diferenças de vocabulário entre as duas

Tudo isto fica contido dentro do adaptador cTrader:

| Conceito | MetaTrader 5 | cTrader Open API |
|---|---|---|
| Volume | lotes (0,05) | **centésimos de unidade** (500.000) |
| Símbolo | nome ("EURUSD") | `symbolId` numérico |
| Posição | ticket inteiro | `positionId` |
| Protocolo | DLL / named pipe (Windows) | JSON sobre TCP+TLS, porta 5036 |

A conversão de volume merece destaque porque **erra em silêncio**: não
levanta exceção, manda uma ordem cem vezes maior. Por isso ela usa o
`lotSize` que a própria corretora informa por símbolo — nunca um 100.000
chutado — arredonda **para baixo** no passo do instrumento (arredondar para
cima aumentaria o risco além do calculado) e recusa antes de enviar se o
resultado ficar fora da faixa da corretora.

## O que está validado e o que não está

**Validado por teste determinístico** (55 testes em `tests/unit/broker/`):
conversões de unidade, sequência de autenticação, resolução de símbolo com
sufixo de corretora, montagem do pedido, arredondamento por dígitos do
instrumento, tradução de posições, guarda de conta nos dois sentidos, e o
comportamento em erro da API.

**NÃO validado contra servidor real**: `app/broker/ctrader/transport.py` — o
socket, o TLS e o enquadramento de mensagens. Essa peça só pode ser
exercitada com credenciais e conta de verdade. Ela está isolada de propósito:
tudo o que erra em silêncio vive acima dela, com teste.

Consequência prática: **o caminho cTrader ainda não enviou uma ordem real**.
Antes de usá-lo com dinheiro, rode em conta demo da cTrader e confira ordem,
stop, alvo e trailing contra a plataforma.

## Como testar o caminho cTrader

1. Registre uma aplicação no portal da cTrader e obtenha `clientId` e
   `clientSecret`.
2. Autorize sua conta de trading e obtenha o `accessToken` e o
   `ctidTraderAccountId`.
3. Configure `BROKER=ctrader` com `CTRADER_ACCOUNT_IS_DEMO=true`.
4. Suba com o modo do sistema em `DEMO` e confira, na plataforma da
   corretora, que a ordem chegou com o volume e a proteção esperados.

## Adicionar outra corretora

Implemente `BrokerPort` e registre em `app/broker/factory.py`. O motor de
decisão não precisa saber que ela existe — é exatamente esse o ponto.
