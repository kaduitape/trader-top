//+------------------------------------------------------------------+
//| AITraderPulse.mq5                                                 |
//| Desenha no grafico do MetaTrader a analise do AI Trader PRO.      |
//|                                                                   |
//| POR QUE ISTO E UM EXPERT ADVISOR, E NAO UM INDICADOR              |
//|                                                                   |
//| `WebRequest()` NAO PODE ser chamada de um indicador. Indicadores  |
//| rodam na thread de interface do terminal, e uma chamada de rede   |
//| ali congelaria a interface inteira — a plataforma simplesmente    |
//| recusa. Um EA roda em thread propria e pode.                      |
//|                                                                   |
//| Entao isto e um EA que se comporta como indicador: ele desenha, e |
//| SO desenha. Nao ha `#include <Trade\Trade.mqh>`, nao ha           |
//| `OrderSend`, nao ha `CTrade` neste arquivo. Anexar ao grafico e   |
//| tao seguro quanto anexar um indicador — a diferenca e que este    |
//| consegue falar com o servidor.                                    |
//|                                                                   |
//| TRES COISAS QUE FALHAM EM SILENCIO SE FOREM IGNORADAS             |
//|                                                                   |
//| 1. A URL precisa estar liberada em Ferramentas > Opcoes >         |
//|    Expert Advisors > "Permitir WebRequest para as URLs listadas". |
//|    Sem isso `WebRequest` devolve -1 com erro 4014 e NADA e        |
//|    desenhado. E a causa numero um de "nao funciona".              |
//|                                                                   |
//| 2. "AutoTrading" precisa estar LIGADO no terminal. Nao porque     |
//|    este EA opere — ele nao opera — mas porque com o botao         |
//|    desligado o terminal nao executa EA nenhum.                    |
//|                                                                   |
//| 3. Os precos vem do servidor no simbolo do PAINEL. Se a corretora |
//|    usa sufixo (EURUSDm, EURUSD.raw), informe em SymbolOverride o  |
//|    nome como ele existe no painel — desenhar niveis de EURUSD num |
//|    grafico de EURUSDm colocaria as zonas no lugar errado sem      |
//|    nenhum aviso.                                                  |
//+------------------------------------------------------------------+
#property copyright "AI Trader PRO"
#property version   "1.00"
#property strict
#property description "Desenha zonas de entrada, take, stop e risco vindas do AI Trader PRO. Nao envia ordens."

//--- Conexao
input string ApiUrl          = "http://127.0.0.1:8000"; // URL do painel (sem barra no fim)
input string ApiKey          = "";                      // Chave de API (Configuracoes > Chaves)
input string SymbolOverride  = "";                      // Simbolo no painel (vazio = o do grafico)
input string TimeframeOverride = "";                    // M1..MN1 (vazio = o do grafico)

//--- Analise
input int    TakeTicks       = 20;      // Take desejado, em ticks
input string Direction       = "AUTO";  // AUTO | COMPRA | VENDA
input int    RefreshSeconds  = 15;      // Intervalo de consulta

//--- Desenho
input bool   ShowHeatZones   = true;    // Faixas de confluencia do mapa de calor
input bool   ShowRiskArea    = true;    // Area alem do stop
input bool   ShowPanel       = true;    // Legenda no canto do grafico
input int    ZoneTransparency = 85;     // 0-100 (maior = mais transparente)

#define PREFIX "AITP_"
#define MAX_ZONES 64

//--- Estado
bool     g_enabled      = true;
bool     g_last_ok      = false;
string   g_headline     = "iniciando...";
string   g_detail       = "";
string   g_error        = "";
datetime g_last_fetch   = 0;
string   g_symbol       = "";
string   g_timeframe    = "";

//+------------------------------------------------------------------+
//| Ciclo de vida                                                     |
//+------------------------------------------------------------------+
int OnInit()
  {
   g_symbol    = (StringLen(SymbolOverride) > 0) ? SymbolOverride : _Symbol;
   g_timeframe = (StringLen(TimeframeOverride) > 0) ? TimeframeOverride : PeriodToName(_Period);

   if(StringLen(ApiKey) == 0)
     {
      // Falha cedo e explicita: sem chave nenhuma consulta funcionaria, e
      // um grafico vazio nao diz por que esta vazio.
      g_error = "Configure ApiKey (painel > Configuracoes > Chaves de API).";
      DrawPanel();
      Print("AITraderPulse: ", g_error);
      return(INIT_SUCCEEDED);
     }

   CreateToggleButton();
   EventSetTimer(MathMax(3, RefreshSeconds));
   Fetch();   // nao esperar o primeiro timer
   return(INIT_SUCCEEDED);
  }

void OnDeinit(const int reason)
  {
   EventKillTimer();
   ClearObjects();
   ChartRedraw();
  }

void OnTimer()
  {
   Fetch();
  }

//+------------------------------------------------------------------+
//| Clique no botao de ligar/desligar                                 |
//+------------------------------------------------------------------+
void OnChartEvent(const int id, const long &lparam, const double &dparam, const string &sparam)
  {
   if(id != CHARTEVENT_OBJECT_CLICK || sparam != PREFIX + "toggle")
      return;

   // O botao volta sozinho ao estado nao-pressionado: quem manda no rotulo
   // e a RESPOSTA do servidor, nao o clique. Se a chamada falhar, o botao
   // nao pode ficar mostrando um estado que o servidor nao tem.
   ObjectSetInteger(0, PREFIX + "toggle", OBJPROP_STATE, false);

   if(Toggle(!g_enabled))
      Fetch();
   ChartRedraw();
  }

//+------------------------------------------------------------------+
//| Rede                                                              |
//+------------------------------------------------------------------+
string BuildUrl()
  {
   return(ApiUrl + "/api/pulso"
          + "?symbol=" + g_symbol
          + "&timeframe=" + g_timeframe
          + "&take_ticks=" + IntegerToString(TakeTicks)
          + "&direction=" + Direction);
  }

//--- Devolve o corpo da resposta, ou "" com g_error preenchido.
string HttpGet(const string url)
  {
   char   corpo[];
   char   resposta[];
   string cabecalhos;
   string envio = "X-API-Key: " + ApiKey + "\r\n";

   ResetLastError();
   int codigo = WebRequest("GET", url, envio, 5000, corpo, resposta, cabecalhos);

   if(codigo == -1)
     {
      int erro = GetLastError();
      if(erro == 4014)
         g_error = "Libere " + ApiUrl + " em Opcoes > Expert Advisors > WebRequest.";
      else
         g_error = "Falha de rede (" + IntegerToString(erro) + "). Painel acessivel?";
      return("");
     }

   string texto = CharArrayToString(resposta, 0, WHOLE_ARRAY, CP_UTF8);

   if(codigo == 401)
     {
      g_error = "Chave de API recusada. Gere outra no painel.";
      return("");
     }
   if(codigo == 404)
     {
      g_error = g_symbol + " sem candles coletadas no painel.";
      return("");
     }
   if(codigo != 200)
     {
      g_error = "Servidor respondeu " + IntegerToString(codigo) + ".";
      return("");
     }

   g_error = "";
   return(texto);
  }

bool Toggle(const bool ligar)
  {
   char   corpo[];
   char   resposta[];
   string cabecalhos;
   string json = "{\"symbol\":\"" + g_symbol + "\",\"enabled\":"
                 + (ligar ? "true" : "false") + "}";

   StringToCharArray(json, corpo, 0, StringLen(json), CP_UTF8);
   // StringToCharArray anexa o terminador nulo; envia-lo faria o servidor
   // ver um byte a mais e recusar o JSON.
   ArrayResize(corpo, StringLen(json));

   string envio = "X-API-Key: " + ApiKey + "\r\nContent-Type: application/json\r\n";

   ResetLastError();
   int codigo = WebRequest("POST", ApiUrl + "/api/pulso/toggle", envio, 5000,
                           corpo, resposta, cabecalhos);
   if(codigo != 200)
     {
      g_error = "Nao foi possivel alternar (codigo " + IntegerToString(codigo) + ").";
      DrawPanel();
      return(false);
     }
   return(true);
  }

//+------------------------------------------------------------------+
//| Leitura do JSON                                                   |
//|                                                                   |
//| MQL5 nao tem parser de JSON. Estas funcoes fazem busca de string  |
//| sobre um objeto RASO — e a API foi desenhada rasa exatamente por  |
//| isso. Nao tente usa-las em JSON aninhado arbitrario.              |
//+------------------------------------------------------------------+
string JsonRaw(const string json, const string chave, const int desde = 0)
  {
   string alvo = "\"" + chave + "\":";
   int inicio = StringFind(json, alvo, desde);
   if(inicio < 0)
      return("");
   inicio += StringLen(alvo);

   while(inicio < StringLen(json) && StringGetCharacter(json, inicio) == ' ')
      inicio++;

   if(StringGetCharacter(json, inicio) == '"')
     {
      inicio++;
      int fim = StringFind(json, "\"", inicio);
      if(fim < 0)
         return("");
      return(StringSubstr(json, inicio, fim - inicio));
     }

   int fim = inicio;
   while(fim < StringLen(json))
     {
      ushort c = StringGetCharacter(json, fim);
      if(c == ',' || c == '}' || c == ']')
         break;
      fim++;
     }
   string bruto = StringSubstr(json, inicio, fim - inicio);
   StringTrimLeft(bruto);
   StringTrimRight(bruto);
   return(bruto);
  }

double JsonNum(const string json, const string chave, const int desde = 0)
  {
   string bruto = JsonRaw(json, chave, desde);
   if(StringLen(bruto) == 0)
      return(0.0);
   return(StringToDouble(bruto));
  }

bool JsonBool(const string json, const string chave, const int desde = 0)
  {
   return(JsonRaw(json, chave, desde) == "true");
  }

//+------------------------------------------------------------------+
//| Consulta e redesenho                                              |
//+------------------------------------------------------------------+
void Fetch()
  {
   if(StringLen(ApiKey) == 0)
      return;

   string json = HttpGet(BuildUrl());
   if(StringLen(json) == 0)
     {
      // Erro de rede NAO apaga o desenho anterior de proposito: um grafico
      // que se esvazia a cada oscilacao de conexao e pior que um que
      // mantem o ultimo cenario e avisa que ele envelheceu.
      g_last_ok = false;
      DrawPanel();
      ChartRedraw();
      return;
     }

   g_last_ok    = true;
   g_last_fetch = TimeCurrent();
   g_enabled    = JsonBool(json, "enabled");
   g_headline   = JsonRaw(json, "headline");

   ClearZones();

   if(!g_enabled)
     {
      g_detail = "Clique em LIGAR para voltar a receber a analise.";
      DrawPanel();
      UpdateButton();
      ChartRedraw();
      return;
     }

   bool   temEntrada = JsonBool(json, "has_entry");
   double entryMin   = JsonNum(json, "entry_min");
   double entryMax   = JsonNum(json, "entry_max");
   double sweet      = JsonNum(json, "sweet_spot");
   bool   temTake    = JsonBool(json, "has_take");
   double take       = JsonNum(json, "take");
   bool   temStop    = JsonBool(json, "has_stop");
   double stop       = JsonNum(json, "stop");
   bool   temNivel   = JsonBool(json, "has_decision_level");
   double nivel      = JsonNum(json, "decision_level");
   bool   velho      = JsonBool(json, "is_stale");
   string vies       = JsonRaw(json, "bias");
   int    distancia  = (int)JsonNum(json, "distance_ticks");

   bool comprando = (vies == "LONG");
   color corLado  = comprando ? clrLimeGreen : clrTomato;

   if(ShowHeatZones)
      DrawHeatZones(json, comprando);

   if(temEntrada)
     {
      DrawZone("entry", entryMin, entryMax, corLado,
               (comprando ? "BUY ZONE " : "SELL ZONE ") + FormatPrice(entryMin)
               + "-" + FormatPrice(entryMax));
      DrawLevel("sweet", sweet, corLado, STYLE_SOLID, 2,
                "MELHOR ENTRADA " + FormatPrice(sweet));
     }

   if(temTake)
      DrawLevel("take", take, clrLimeGreen, STYLE_DASH, 2,
                "TAKE +" + IntegerToString(TakeTicks) + " ticks " + FormatPrice(take));

   if(temStop)
     {
      DrawLevel("stop", stop, clrRed, STYLE_DASH, 2,
                "STOP / INVALIDACAO " + FormatPrice(stop));
      if(ShowRiskArea)
         DrawRiskArea(stop, comprando);
     }

   if(temNivel)
      DrawLevel("decision", nivel, clrMediumPurple, STYLE_DOT, 1,
                "DECISION LEVEL " + FormatPrice(nivel));

   g_detail = BuildDetail(velho, temEntrada, distancia, json);
   DrawPanel();
   UpdateButton();
   ChartRedraw();
  }

string BuildDetail(const bool velho, const bool temEntrada, const int distancia,
                   const string json)
  {
   if(velho)
      return("DADOS DESATUALIZADOS ha "
             + DoubleToString(JsonNum(json, "data_age_minutes"), 0)
             + " min - coletor MT5 parado");

   string status = JsonRaw(json, "status");
   if(status == "READY")
      return("Preco dentro da zona.");
   if(status == "WAIT_PULLBACK" && temEntrada)
      return("Aguardar: " + IntegerToString(distancia) + " ticks ate a zona.");
   if(status == "MISSED")
      return("O preco ja passou pela zona.");
   if(status == "NO_SETUP")
      return("Sem entrada boa agora.");
   return("");
  }

//+------------------------------------------------------------------+
//| Desenho                                                           |
//+------------------------------------------------------------------+
void DrawHeatZones(const string json, const bool comprando)
  {
   // Percorre `zones[]` procurando os itens HEAT. Cada iteracao avanca a
   // partir da posicao do item anterior — sem isso `StringFind` acharia
   // sempre o primeiro e o laco nunca terminaria.
   int posicao = StringFind(json, "\"zones\"");
   if(posicao < 0)
      return;

   int desenhadas = 0;
   while(desenhadas < MAX_ZONES)
     {
      int item = StringFind(json, "\"kind\":", posicao);
      if(item < 0)
         break;
      posicao = item + 7;

      string tipo = JsonRaw(json, "kind", item);
      if(tipo != "HEAT")
         continue;

      double preco = JsonNum(json, "price_min", item);
      string cor   = JsonRaw(json, "color", item);
      if(preco <= 0.0)
         continue;

      DrawLevel("heat" + IntegerToString(desenhadas), preco,
                (cor == "GREEN") ? clrSeaGreen : clrGoldenrod,
                STYLE_DOT, 1, "");
      desenhadas++;
     }
  }

void DrawZone(const string id, const double p1, const double p2, const color cor,
              const string texto)
  {
   string nome = PREFIX + id;
   datetime t1 = ChartFirstVisibleTime();
   datetime t2 = TimeCurrent() + PeriodSeconds(_Period) * 30;

   if(ObjectFind(0, nome) < 0)
      ObjectCreate(0, nome, OBJ_RECTANGLE, 0, t1, p1, t2, p2);

   ObjectSetInteger(0, nome, OBJPROP_TIME, 0, t1);
   ObjectSetDouble(0, nome, OBJPROP_PRICE, 0, p1);
   ObjectSetInteger(0, nome, OBJPROP_TIME, 1, t2);
   ObjectSetDouble(0, nome, OBJPROP_PRICE, 1, p2);
   ObjectSetInteger(0, nome, OBJPROP_COLOR, cor);
   ObjectSetInteger(0, nome, OBJPROP_FILL, true);
   ObjectSetInteger(0, nome, OBJPROP_BACK, true);
   ObjectSetInteger(0, nome, OBJPROP_SELECTABLE, false);
   ObjectSetString(0, nome, OBJPROP_TOOLTIP, texto);

   if(StringLen(texto) > 0)
      DrawTag(id + "_tag", MathMax(p1, p2), cor, texto);
  }

void DrawRiskArea(const double stop, const bool comprando)
  {
   // Numa compra a invalidacao e ABAIXO. Pintar do lado errado marcaria
   // como perigosa exatamente a regiao do alvo.
   double limite = comprando
                   ? stop - 100 * _Point * MathMax(1, TakeTicks)
                   : stop + 100 * _Point * MathMax(1, TakeTicks);

   string nome = PREFIX + "risk";
   datetime t1 = ChartFirstVisibleTime();
   datetime t2 = TimeCurrent() + PeriodSeconds(_Period) * 30;

   if(ObjectFind(0, nome) < 0)
      ObjectCreate(0, nome, OBJ_RECTANGLE, 0, t1, stop, t2, limite);

   ObjectSetInteger(0, nome, OBJPROP_TIME, 0, t1);
   ObjectSetDouble(0, nome, OBJPROP_PRICE, 0, stop);
   ObjectSetInteger(0, nome, OBJPROP_TIME, 1, t2);
   ObjectSetDouble(0, nome, OBJPROP_PRICE, 1, limite);
   ObjectSetInteger(0, nome, OBJPROP_COLOR, clrFireBrick);
   ObjectSetInteger(0, nome, OBJPROP_FILL, true);
   ObjectSetInteger(0, nome, OBJPROP_BACK, true);
   ObjectSetInteger(0, nome, OBJPROP_SELECTABLE, false);
   ObjectSetString(0, nome, OBJPROP_TOOLTIP, "Zona de risco: alem da invalidacao");
  }

void DrawLevel(const string id, const double preco, const color cor,
               const ENUM_LINE_STYLE estilo, const int largura, const string texto)
  {
   string nome = PREFIX + id;
   if(ObjectFind(0, nome) < 0)
      ObjectCreate(0, nome, OBJ_HLINE, 0, 0, preco);

   ObjectSetDouble(0, nome, OBJPROP_PRICE, preco);
   ObjectSetInteger(0, nome, OBJPROP_COLOR, cor);
   ObjectSetInteger(0, nome, OBJPROP_STYLE, estilo);
   ObjectSetInteger(0, nome, OBJPROP_WIDTH, largura);
   ObjectSetInteger(0, nome, OBJPROP_BACK, false);
   ObjectSetInteger(0, nome, OBJPROP_SELECTABLE, false);
   ObjectSetString(0, nome, OBJPROP_TOOLTIP, texto);

   if(StringLen(texto) > 0)
      DrawTag(id + "_tag", preco, cor, texto);
  }

void DrawTag(const string id, const double preco, const color cor, const string texto)
  {
   string nome = PREFIX + id;
   datetime quando = TimeCurrent() + PeriodSeconds(_Period) * 3;

   if(ObjectFind(0, nome) < 0)
      ObjectCreate(0, nome, OBJ_TEXT, 0, quando, preco);

   ObjectSetInteger(0, nome, OBJPROP_TIME, quando);
   ObjectSetDouble(0, nome, OBJPROP_PRICE, preco);
   ObjectSetString(0, nome, OBJPROP_TEXT, " " + texto);
   ObjectSetInteger(0, nome, OBJPROP_COLOR, cor);
   ObjectSetInteger(0, nome, OBJPROP_FONTSIZE, 8);
   ObjectSetInteger(0, nome, OBJPROP_ANCHOR, ANCHOR_LEFT);
   ObjectSetInteger(0, nome, OBJPROP_SELECTABLE, false);
  }

void DrawPanel()
  {
   if(!ShowPanel)
      return;

   string linhas[3];
   linhas[0] = g_headline;
   linhas[1] = (StringLen(g_error) > 0) ? g_error : g_detail;
   linhas[2] = (g_last_fetch > 0)
               ? ("atualizado " + TimeToString(g_last_fetch, TIME_SECONDS))
               : "sem resposta ainda";

   color cores[3];
   cores[0] = g_enabled ? clrWhite : clrSilver;
   cores[1] = (StringLen(g_error) > 0) ? clrOrangeRed : clrLightGray;
   cores[2] = g_last_ok ? clrMediumSeaGreen : clrOrangeRed;

   for(int i = 0; i < 3; i++)
     {
      string nome = PREFIX + "panel" + IntegerToString(i);
      if(ObjectFind(0, nome) < 0)
         ObjectCreate(0, nome, OBJ_LABEL, 0, 0, 0);

      ObjectSetInteger(0, nome, OBJPROP_CORNER, CORNER_LEFT_UPPER);
      ObjectSetInteger(0, nome, OBJPROP_XDISTANCE, 12);
      ObjectSetInteger(0, nome, OBJPROP_YDISTANCE, 20 + i * 16);
      ObjectSetString(0, nome, OBJPROP_TEXT, linhas[i]);
      ObjectSetInteger(0, nome, OBJPROP_COLOR, cores[i]);
      ObjectSetInteger(0, nome, OBJPROP_FONTSIZE, (i == 0) ? 11 : 8);
      ObjectSetInteger(0, nome, OBJPROP_SELECTABLE, false);
     }
  }

void CreateToggleButton()
  {
   string nome = PREFIX + "toggle";
   if(ObjectFind(0, nome) < 0)
      ObjectCreate(0, nome, OBJ_BUTTON, 0, 0, 0);

   ObjectSetInteger(0, nome, OBJPROP_CORNER, CORNER_LEFT_UPPER);
   ObjectSetInteger(0, nome, OBJPROP_XDISTANCE, 12);
   ObjectSetInteger(0, nome, OBJPROP_YDISTANCE, 72);
   ObjectSetInteger(0, nome, OBJPROP_XSIZE, 150);
   ObjectSetInteger(0, nome, OBJPROP_YSIZE, 24);
   ObjectSetInteger(0, nome, OBJPROP_FONTSIZE, 9);
   ObjectSetInteger(0, nome, OBJPROP_SELECTABLE, false);
   UpdateButton();
  }

void UpdateButton()
  {
   string nome = PREFIX + "toggle";
   if(ObjectFind(0, nome) < 0)
      return;

   // O rotulo diz a ACAO do clique, nao o estado atual: "DESLIGAR" num
   // botao ligado e ambiguo o suficiente para alguem clicar sem querer.
   ObjectSetString(0, nome, OBJPROP_TEXT, g_enabled ? "DESLIGAR IA" : "LIGAR IA");
   ObjectSetInteger(0, nome, OBJPROP_BGCOLOR, g_enabled ? clrSeaGreen : clrDimGray);
   ObjectSetInteger(0, nome, OBJPROP_COLOR, clrWhite);
  }

//+------------------------------------------------------------------+
//| Limpeza                                                           |
//+------------------------------------------------------------------+
void ClearZones()
  {
   // Apaga so o desenho da analise; o painel e o botao sobrevivem, porque
   // sao eles que explicam o que aconteceu quando nao ha o que desenhar.
   for(int i = ObjectsTotal(0) - 1; i >= 0; i--)
     {
      string nome = ObjectName(0, i);
      if(StringFind(nome, PREFIX) != 0)
         continue;
      if(StringFind(nome, PREFIX + "panel") == 0 || nome == PREFIX + "toggle")
         continue;
      ObjectDelete(0, nome);
     }
  }

void ClearObjects()
  {
   for(int i = ObjectsTotal(0) - 1; i >= 0; i--)
     {
      string nome = ObjectName(0, i);
      if(StringFind(nome, PREFIX) == 0)
         ObjectDelete(0, nome);
     }
  }

//+------------------------------------------------------------------+
//| Utilidades                                                        |
//+------------------------------------------------------------------+
datetime ChartFirstVisibleTime()
  {
   datetime tempos[];
   int primeira = (int)ChartGetInteger(0, CHART_FIRST_VISIBLE_BAR);
   if(CopyTime(_Symbol, _Period, primeira, 1, tempos) == 1)
      return(tempos[0]);
   return(TimeCurrent() - PeriodSeconds(_Period) * 100);
  }

string FormatPrice(const double preco)
  {
   return(DoubleToString(preco, _Digits));
  }

string PeriodToName(const ENUM_TIMEFRAMES periodo)
  {
   switch(periodo)
     {
      case PERIOD_M1:  return("M1");
      case PERIOD_M5:  return("M5");
      case PERIOD_M15: return("M15");
      case PERIOD_M30: return("M30");
      case PERIOD_H1:  return("H1");
      case PERIOD_H4:  return("H4");
      case PERIOD_D1:  return("D1");
      case PERIOD_W1:  return("W1");
      case PERIOD_MN1: return("MN1");
     }
   // Timeframe fora da matriz de analise do painel: cair para M15 seria
   // desenhar um cenario de outro periodo sem avisar.
   Print("AITraderPulse: periodo do grafico nao suportado pelo painel; use TimeframeOverride.");
   return("M15");
  }
//+------------------------------------------------------------------+
