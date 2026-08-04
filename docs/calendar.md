# Calendário econômico — o portão que nunca disparou

## O bug

O sistema tinha, desde sempre, um filtro que deveria impedir entrada em cima
de evento de alto impacto. Ele estava escrito assim:

```python
high_impact_deadline = resolved_now + timedelta(minutes=60)
if any(
    item.impact == "HIGH"
    and resolved_now <= item.published_at <= high_impact_deadline
    for item in news_assessment.items
):
```

Dois defeitos, e cada um sozinho já bastava para anular a proteção:

**1. Exigia data no futuro.** `published_at` é a data em que uma **notícia
foi publicada** — sempre no passado. A condição nunca foi verdadeira. Esse
portão **nunca bloqueou uma única entrada**, nem com a API paga funcionando
perfeitamente.

**2. Ignorava a moeda.** O campo `item.currency` existia e não era usado. Se
o primeiro defeito não existisse, um dado do iene teria bloqueado uma entrada
em EUR/USD. Um portão que barra o que não deveria é tão ruim quanto um que
não barra nada.

A raiz dos dois é a mesma: **notícia e calendário foram tratados como a mesma
coisa.** Manchete tem hora de publicação (passado); evento econômico tem hora
de agendamento (futuro). Agora são tipos diferentes — `NewsItem` e
`CalendarEvent` — porque confundi-los foi o bug.

## Como funciona agora

`app/calendar_feed/` traz a fonte, e `blackout.py` a decisão:

- **Compara com `scheduled_at`**, o horário agendado.
- **Filtra pelas moedas do instrumento**: EUR/USD só é afetado por eventos de
  EUR ou USD. Ouro (XAU/USD) só por USD — XAU não tem banco central.
- **Bloqueia dos dois lados do evento**: 30 minutos antes e 15 depois, ambos
  configuráveis. O perigo não acaba na divulgação: logo depois o spread abre
  e o preço chicoteia.
- **Diz qual evento** está barrando e a que horas, em vez de um genérico
  "bloqueado por notícia".

Dois cuidados que valem registrar:

- Evento **sem moeda declarada** (feriado bancário, decisão de organismo
  internacional) é tratado como global e bloqueia. Melhor barrar do que
  ignorar.
- Símbolo cujas moedas o sistema **não consegue deduzir** (`US30`, `USTECH`)
  também bloqueia, em vez de filtrar por siglas inventadas. A dedução só
  divide um nome de seis letras quando os dois lados são códigos ISO
  conhecidos.

## Quando o calendário não está disponível

**O robô continua operando.** Decisão explícita do dono do sistema.

O raciocínio: travar por falta de dado externo recriaria exatamente o
problema que o projeto acabou de resolver — um sistema que não opera porque
uma fonte caiu. A ausência não some, porém: aparece no status e conta no
relatório "por que não operei hoje".

Quem preferir o lado paranoico inverte com
`CALENDAR_BLOCK_WHEN_UNAVAILABLE=true`.

## Eficiência

O portão do calendário roda **antes** da API paga, junto com as outras
verificações locais. Se um evento já barra a entrada, a MarketPulse nem é
consultada.

A leitura fica em cache por 15 minutos (`CALENDAR_CACHE_TTL_SECONDS`). Sem
isso o sistema abriria e parsearia o arquivo a cada ciclo do piloto — a cada
15 segundos, para uma agenda que muda uma vez por dia. Falha nunca entra no
cache, pela razão de sempre: congelar uma falha esconderia que o exportador
parou.

## Configurar

```ini
CALENDAR_FILE_PATH=C:\trader-top\data\calendar.json
CALENDAR_BLACKOUT_BEFORE_MINUTES=30
CALENDAR_BLACKOUT_AFTER_MINUTES=15
CALENDAR_MIN_IMPACT=HIGH
CALENDAR_MAX_AGE_HOURS=36
CALENDAR_BLOCK_WHEN_UNAVAILABLE=false
```

Vazio desliga o filtro.

## A fonte: o calendário do próprio MetaTrader

O MT5 já traz um calendário econômico completo, e ele está na máquina onde o
conector roda. Um Expert Advisor pequeno exporta para JSON e o sistema lê —
sem dependência de terceiro, sem Cloudflare, sem cota.

Formato esperado (lista, ou objeto com a chave `events`):

```json
[
  {
    "title": "Non-Farm Payrolls",
    "scheduled_at": "2026-09-04T12:30:00Z",
    "currency": "USD",
    "impact": "HIGH",
    "forecast": 165000,
    "previous": 142000,
    "actual": null
  }
]
```

Regras de leitura, todas cobertas por teste:

| Situação | Resultado |
|---|---|
| Arquivo ausente | `NOT_CONFIGURED` — não bloqueia, mas avisa |
| Arquivo com mais de 36h sem atualização | **ERRO** — calendário velho é pior que nenhum |
| JSON quebrado ou sem a chave `events` | **ERRO** |
| Um registro inválido no meio | Descartado; os válidos seguem |
| `"-"` em previsão | Vira `None`, nunca `0.0` |
| `"165,000"` / `"3.5%"` | Números entendidos |

`forecast` e `actual` são lidos e guardados mas **não são usados hoje**. Eles
abrem caminho para medir surpresa (`actual − forecast`), que é fundamento
quantitativo de verdade — deliberadamente fora deste portão, que é uma
proteção binária.
