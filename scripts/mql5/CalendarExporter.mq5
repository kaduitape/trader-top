//+------------------------------------------------------------------+
//| CalendarExporter.mq5                                              |
//| Exporta o calendario economico NATIVO do MetaTrader 5 para JSON. |
//|                                                                   |
//| O AI Trader PRO le esse arquivo para bloquear entradas em cima de |
//| evento de alto impacto (ver docs/calendar.md e                    |
//| app/calendar_feed/file_source.py). Fonte gratuita, sem terceiro,  |
//| sem cota: o calendario ja esta no terminal.                      |
//|                                                                   |
//| DOIS DETALHES QUE ERRAM EM SILENCIO SE FOREM IGNORADOS:          |
//|                                                                   |
//| 1. Os horarios do calendario vem no fuso do SERVIDOR DE TRADE,   |
//|    nao em UTC. Exportar sem converter deslocaria toda a janela de|
//|    bloqueio pelo offset da corretora — o robo evitaria o horario |
//|    errado e entraria exatamente em cima da noticia.              |
//|                                                                   |
//| 2. Os valores (previsto/efetivo/anterior) sao inteiros           |
//|    multiplicados por 1.000.000, e LONG_MIN significa "sem valor".|
//|    Dividir sem checar LONG_MIN produziria numeros absurdos.      |
//+------------------------------------------------------------------+
#property copyright "AI Trader PRO"
#property version   "1.00"
#property strict

input int    ExportPeriodMinutes = 15;    // Frequencia de exportacao (minutos)
input int    DaysAhead           = 3;     // Dias a frente a exportar
input int    DaysBack            = 1;     // Dias para tras (eventos ja divulgados)
input string FileName            = "calendar.json"; // Nome do arquivo
input bool   UseCommonFolder     = true;  // Pasta comum (caminho previsivel)
input bool   OnlyMediumAndHigh   = true;  // Descartar eventos irrelevantes
input bool   VerboseLog          = false; // Registrar cada exportacao

//+------------------------------------------------------------------+
int OnInit()
  {
   if(!TerminalInfoInteger(TERMINAL_CONNECTED))
      Print("CalendarExporter: terminal desconectado; a primeira exportacao pode falhar.");

   EventSetTimer(MathMax(60, ExportPeriodMinutes * 60));
   ExportCalendar();   // nao esperar o primeiro timer
   return(INIT_SUCCEEDED);
  }

void OnDeinit(const int reason)
  {
   EventKillTimer();
  }

void OnTimer()
  {
   ExportCalendar();
  }

//+------------------------------------------------------------------+
//| Diferenca entre o servidor de trade e o GMT, em segundos.        |
//|                                                                  |
//| Usa o offset ATUAL para converter tambem os eventos dos proximos |
//| dias. Numa virada de horario de verao dentro da janela isso erra |
//| por uma hora — por isso a janela de bloqueio tem folga dos dois  |
//| lados, e por isso a exportacao roda a cada 15 minutos.           |
//+------------------------------------------------------------------+
int ServerToGmtOffsetSeconds()
  {
   return((int)(TimeTradeServer() - TimeGMT()));
  }

string ToIso8601Utc(datetime server_time)
  {
   datetime utc = server_time - ServerToGmtOffsetSeconds();
   MqlDateTime parts;
   TimeToStruct(utc, parts);
   return(StringFormat("%04d-%02d-%02dT%02d:%02d:%02dZ",
                       parts.year, parts.mon, parts.day,
                       parts.hour, parts.min, parts.sec));
  }

//+------------------------------------------------------------------+
//| Escapa o que quebraria o JSON. Nome de evento traz aspas e       |
//| barras com mais frequencia do que se imagina ("Fed's Powell").   |
//+------------------------------------------------------------------+
string JsonEscape(const string raw)
  {
   string saida = "";
   int total = StringLen(raw);
   for(int i = 0; i < total; i++)
     {
      ushort c = StringGetCharacter(raw, i);
      if(c == '"')            saida += "\\\"";
      else if(c == '\\')      saida += "\\\\";
      else if(c == '\n')      saida += "\\n";
      else if(c == '\r')      saida += "\\r";
      else if(c == '\t')      saida += "\\t";
      else if(c < 32)         saida += StringFormat("\\u%04x", c);
      else                    saida += ShortToString(c);
     }
   return(saida);
  }

//+------------------------------------------------------------------+
//| Valor do calendario: inteiro x 1.000.000, LONG_MIN = ausente.    |
//| Devolve "null" (e nao 0) quando nao ha valor — inventar zero      |
//| seria publicar um numero que ninguem divulgou.                    |
//+------------------------------------------------------------------+
string ValueOrNull(const long raw, const int digits)
  {
   if(raw == LONG_MIN)
      return("null");
   double valor = (double)raw / 1000000.0;
   return(DoubleToString(valor, MathMax(0, MathMin(8, digits))));
  }

string ImportanceToText(const ENUM_CALENDAR_EVENT_IMPORTANCE importance)
  {
   switch(importance)
     {
      case CALENDAR_IMPORTANCE_HIGH:     return("HIGH");
      case CALENDAR_IMPORTANCE_MODERATE: return("MEDIUM");
      case CALENDAR_IMPORTANCE_LOW:      return("LOW");
      default:                           return("NONE");
     }
  }

//+------------------------------------------------------------------+
void ExportCalendar()
  {
   datetime agora = TimeTradeServer();
   datetime inicio = agora - (datetime)(DaysBack * 86400);
   datetime fim    = agora + (datetime)(DaysAhead * 86400);

   MqlCalendarValue valores[];
   int total = CalendarValueHistory(valores, inicio, fim);
   if(total <= 0)
     {
      // Nao escreve arquivo vazio: o lado Python trata arquivo velho como
      // ERRO, e isso e melhor do que um "dia limpo" falso.
      Print("CalendarExporter: CalendarValueHistory retornou ", total,
            " (erro ", GetLastError(), "). Arquivo anterior preservado.");
      return;
     }

   string json = "[";
   int exportados = 0;

   for(int i = 0; i < total; i++)
     {
      MqlCalendarEvent evento;
      if(!CalendarEventById(valores[i].event_id, evento))
         continue;

      if(OnlyMediumAndHigh &&
         evento.importance != CALENDAR_IMPORTANCE_HIGH &&
         evento.importance != CALENDAR_IMPORTANCE_MODERATE)
         continue;

      MqlCalendarCountry pais;
      string moeda = "";
      if(CalendarCountryById(evento.country_id, pais))
         moeda = pais.currency;

      if(exportados > 0)
         json += ",";

      json += "\n  {";
      json += "\"title\":\"" + JsonEscape(evento.name) + "\",";
      json += "\"scheduled_at\":\"" + ToIso8601Utc(valores[i].time) + "\",";
      json += "\"currency\":\"" + JsonEscape(moeda) + "\",";
      json += "\"impact\":\"" + ImportanceToText(evento.importance) + "\",";
      json += "\"actual\":" + ValueOrNull(valores[i].actual_value, evento.digits) + ",";
      json += "\"forecast\":" + ValueOrNull(valores[i].forecast_value, evento.digits) + ",";
      json += "\"previous\":" + ValueOrNull(valores[i].prev_value, evento.digits);
      json += "}";

      exportados++;
     }
   json += "\n]\n";

   if(WriteAtomic(json))
     {
      if(VerboseLog)
         Print("CalendarExporter: ", exportados, " evento(s) exportado(s) para ", FileName,
               " (offset servidor->GMT: ", ServerToGmtOffsetSeconds() / 3600, "h)");
     }
  }

//+------------------------------------------------------------------+
//| Escreve num temporario e so entao substitui o definitivo.        |
//|                                                                  |
//| O leitor roda em outro processo e pode ler no meio da escrita.   |
//| Ele trataria o JSON truncado como ERRO (nao como dia limpo), mas |
//| provocar esse erro a cada exportacao seria ruido desnecessario.  |
//+------------------------------------------------------------------+
bool WriteAtomic(const string conteudo)
  {
   string temporario = FileName + ".tmp";
   int flags = FILE_WRITE | FILE_BIN;
   if(UseCommonFolder)
      flags |= FILE_COMMON;

   int handle = FileOpen(temporario, flags);
   if(handle == INVALID_HANDLE)
     {
      Print("CalendarExporter: nao consegui abrir ", temporario, " (erro ", GetLastError(), ")");
      return(false);
     }

   // UTF-8 explicito: nomes de eventos trazem acentos, e ANSI os corromperia.
   uchar bytes[];
   int tamanho = StringToCharArray(conteudo, bytes, 0, WHOLE_ARRAY, CP_UTF8);
   if(tamanho > 0)
      tamanho--;   // descarta o terminador nulo
   FileWriteArray(handle, bytes, 0, tamanho);
   FileClose(handle);

   int destino_flags = UseCommonFolder ? FILE_COMMON : 0;
   if(!FileMove(temporario, destino_flags, FileName, destino_flags | FILE_REWRITE))
     {
      Print("CalendarExporter: nao consegui substituir ", FileName,
            " (erro ", GetLastError(), ")");
      return(false);
     }
   return(true);
  }
//+------------------------------------------------------------------+
