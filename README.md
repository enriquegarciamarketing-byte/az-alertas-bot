# AZ Inversión · Alertas Bursátiles

Bot que publica noticias flash de mercado en el canal de Telegram **@AZ_Inversion** con el estilo de AZ (titular en negrita con $TICKER y un solo párrafo, sin dos puntos ni coletillas).

Cada 15 minutos GitHub Actions
1. lee 18 fuentes financieras (CNBC, MarketWatch, Yahoo Finance, Seeking Alpha, Nikkei Asia, Expansión, Cinco Días, elEconomista, Europa Press, Investing, Google News con Reuters...)
2. descarta lo ya visto y lo de hace más de 3 horas
3. pasa las nuevas a Gemini, que puntúa de 1 a 10 y redacta solo las de 8 o más (de 9 o más de 00.00 a 07.00)
4. limpia el formato (dos puntos, rayas, coletillas, negritas y emojis de más)
5. publica como máximo 3 por pasada y 20 al día

## Coste 0 €

- **GitHub Actions** gratis e ilimitado en repositorio público. Los secretos no se ven aunque el repo sea público.
- **Gemini** en capa gratuita. La clave se crea en Google AI Studio **sin activar facturación**, así que no hay forma de que te cobren. Si se acaba la cuota del día, el bot prueba el siguiente modelo gratuito y si no hay ninguno espera al día siguiente.
- **Telegram** gratis.

No actives nunca la facturación en el proyecto de Google Cloud de la clave.

## Secretos (Settings → Secrets and variables → Actions)

| Nombre | Valor |
|---|---|
| `TELEGRAM_BOT_TOKEN` | token de Alertas Bursátiles (BotFather → /mybots → API Token) |
| `GEMINI_API_KEY` | clave de aistudio.google.com/apikey |

## Probar

Actions → Alertas Bursátiles → Run workflow
- `test` publica ya la mejor noticia disponible en el canal
- `dry` no publica nada, enseña en el log lo que habría publicado
- `normal` igual que la ejecución automática

## Ajustes (config.json)

- `min_score`, `min_score_night` umbral de calidad (subir a 9 = menos noticias y más gordas)
- `max_posts_per_run`, `daily_post_cap` volumen
- `feeds` añadir o quitar fuentes
- `models` orden de modelos gratuitos de Gemini
- El estilo de redacción está en `prompt.md`

## Pausar

Actions → Alertas Bursátiles → menú `···` → Disable workflow.
