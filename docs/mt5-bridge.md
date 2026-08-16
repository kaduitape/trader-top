# Ponte para o MetaTrader em container (mt5-wine)

Para **subir** os serviços, ver `docs/mt5-wine-docker.md`. Este documento
explica o que a ponte é, como apontá-la pelo painel e como descobrir por que
ela não conecta.

## O problema que ela resolve

O pacote `MetaTrader5` não é uma API da corretora: é um cliente local que
fala com o **terminal instalado**, e só publica wheel para Windows. O painel
roda em container Linux. Por isso o desenho original delegava tudo a um
worker Windows — nenhuma quantidade de código no painel faria uma requisição
HTTP no Linux abrir sessão MT5.

Com o terminal rodando sob Wine em um container, essa premissa cai. A ponte
expõe o módulo `MetaTrader5` de dentro do Wine por RPyC:

```
painel (Linux) → RPyC → Python do Wine → MetaTrader5 → terminal → corretora
```

O que volta pela ponte é um **proxy do módulo**: responde `initialize`,
`login`, `account_info` e `last_error` igual ao pacote nativo. Nada em
`MT5ConnectionService` sabe de que lado está — a diferença termina em
`app/mt5/bridge.py`.

## Segurança: a porta 18812 nunca vai para a internet

RPyC clássico permite **executar código** do outro lado. Quem alcança a
porta da ponte controla a máquina do MetaTrader — e essa máquina tem a sua
conta de corretora aberta.

- A porta deve escutar **só na rede interna do Docker**.
- Nada de `-p 18812:18812` em interface pública, nada de liberar no firewall
  da VPS.
- Para alcançar de outra máquina: túnel SSH (`ssh -L 18812:mt5:18812 …`) ou
  VPN. Nunca porta publicada.

O endereço HTTPS que a Hostinger fornece (`https://…hstgr.cloud/`) é a
interface **noVNC**, para você ver a tela do MetaTrader pelo navegador. Ele
não é a ponte e não serve como host aqui.

## Configurando

### Terminal em outro container (o caso comum)

O `docker compose up` padrão sobe **só `db` e `app`**. Os serviços
`mt5-wine` e `mt5-worker` são opcionais e ficam atrás do profile
`local-mt5` — quem já tem um container de MetaTrader rodando não quer um
segundo terminal disputando a mesma conta na corretora.

Dois passos, e o primeiro é de rede.

**1. Ponha o painel na rede do MetaTrader.** No `.env`:

```ini
MT5_NETWORK=metatrader-5-9p2b_default    # veja com `docker network ls`
```

Isso é duradouro: o deploy recria o container `app`, e uma rede anexada à
mão com `docker network connect` se perderia exatamente aí. Com
`MT5_NETWORK`, o Compose reanexa a cada subida.

**2. Aponte a ponte** em **Configurações → Conexão MetaTrader 5**:

| Campo | Valor |
|---|---|
| Host da ponte | nome do **container** do MetaTrader (`docker ps`) |
| Porta da ponte | a porta RPyC daquela imagem — ver abaixo |
| Login / Senha / Servidor | os da conta na corretora |

O que está salvo no painel tem precedência sobre `MT5_BRIDGE_HOST` do
`.env`, para que "salvei na tela e não mudou nada" nunca aconteça.

Com o host preenchido, o botão **Testar conexão** roda no próprio painel:
não exige o worker Windows.

### Usando o profile local (terminal sob Wine neste compose)

```bash
docker compose --profile local-mt5 up -d
```

Aí o host da ponte é `mt5-wine` e a porta `18812`.

#### A porta não é sempre 18812

Não existe padrão. `mt5linux` documenta **18812**; a imagem
`gmag11/metatrader5_vnc` — a mais usada para rodar o terminal sob Wine com
noVNC — publica o mesmo servidor RPyC em **8001**, e o 3000 é só a tela.

Confira com `docker ps` qual porta o seu container expõe. O diagnóstico
sonda as duas conhecidas, então "porta errada" não se disfarça de
"container ausente".

#### Os dois containers precisam se enxergar

O nome do container só resolve para quem está na **mesma rede Docker**. Se
o MetaTrader subiu por outro `docker compose`, ele está em outra rede por
padrão. Duas saídas:

1. Conectar o container do MT5 à rede do painel (uma vez só):

   ```bash
   docker network connect trader-top_default <container-do-mt5>
   ```

   Confira os nomes com `docker network ls` e `docker ps`.

   **Esta direção, e não a inversa.** O deploy recria o container `app`, e
   toda rede anexada por fora do compose se perde nessa hora. O container
   do MetaTrader não é recriado pelo deploy do painel — então ligá-lo à
   rede do painel sobrevive às atualizações; o contrário, não.

2. Se o container do MT5 publica a 18812 no host (`127.0.0.1:18812`), use
   `host.docker.internal` como host da ponte — o `docker-compose.yml` do
   painel já mapeia esse nome para o gateway.

## Quando não conecta

Use **Diagnosticar ponte** (Configurações → Diagnóstico MT5) ou, no
terminal:

```bash
python -m app.cli mt5 bridge --host mt5 --port 18812
```

São seis verificações em ordem, parando na primeira que falhar — porque
"não conecta" não é diagnóstico, e causas diferentes exigem correções
diferentes:

| Passo | Falhou = |
|---|---|
| Biblioteca `rpyc` | imagem do painel antiga; `docker compose up -d --build` |
| Nome resolve | containers em redes Docker diferentes |
| Porta acessível | container parado, porta só interna, ou porta errada |
| Ponte RPyC | porta certa, serviço errado atrás dela |
| Módulo `MetaTrader5` | ponte de pé, mas o Python do Wine não tem o pacote |
| Terminal responde | MetaTrader fechado dentro do container (abra pelo noVNC) |

O diagnóstico nunca imprime a senha, nem mascarada.

## O que continua no worker Windows

A coleta de ticks. Cada chamada pela ponte é uma ida e volta em socket:
irrelevante para testar conexão e ler conta, caro em laço apertado. Se você
usa a ponte, o worker Windows deixa de ser necessário para configurar e
testar — não para tudo.
