# MT5 no Docker local (Wine)

Este modo substitui o conector Windows por tres servicos no mesmo Compose:

```text
app (painel/API) -> mt5-worker -> mt5-wine (Wine + terminal MT5) -> corretora
                         \-> db
```

O terminal roda em Wine dentro de `mt5-wine`; a aplicacao e o worker falam
com ele pela ponte interna `mt5linux`. A porta de automacao (`18812`) nunca e
publicada no host. Somente a interface noVNC fica disponivel em
`http://localhost:8081` para diagnostico e primeiro login.

## Configurar

No `.env`, preencha os dados da corretora e uma senha distinta para a tela
local do terminal:

```ini
MT5_LOGIN=12345678
MT5_PASSWORD=sua_senha_da_corretora
MT5_SERVER=Nome-Exato-Do-Servidor
MT5_VNC_PASSWORD=uma-senha-forte-e-distinta
MT5_VNC_PORT=8081
```

O nome do servidor precisa ser idêntico ao exibido pelo MetaTrader. Não use a
senha do dashboard nem a senha do MySQL como `MT5_PASSWORD`.

## Iniciar

```powershell
docker compose up -d --build
docker compose logs -f mt5-wine mt5-worker
```

Na primeira inicialização, abra `http://localhost:8081`, informe a senha
`MT5_VNC_PASSWORD` e confirme que o terminal entrou na conta esperada. Faça o
primeiro teste em conta demo e só então habilite qualquer automação.

## Limites

Esta ponte usa Wine e um componente externo (`mt5linux`), não a biblioteca
oficial diretamente no Linux. Ela é útil para operação local/Ubuntu, mas deve
ser acompanhada pelo log do worker e validada novamente após atualizações do
Wine, MT5 ou da corretora.
