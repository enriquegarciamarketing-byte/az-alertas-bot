# AZ Inversión · Alertas Bursátiles

Bot que publica noticias flash de actualidad de mercados en el canal de Telegram **@AZ_Inversion** con el estilo de AZ. Cubre macro, bancos centrales, geopolítica (Ormuz, Irán, aranceles...), materias primas, empresas y cripto. Titular en negrita con $TICKER o bandera/emoji de categoría y un solo párrafo, sin dos puntos ni coletillas.

Cada 15 minutos GitHub Actions MIRA las fuentes, pero solo publica cuando algo lo merece
1. lee 27 fuentes (CNBC, MarketWatch, Seeking Alpha, Nikkei Asia, CoinDesk, Investing, Expansión, Cinco Días, Europa Press, Google News con Reuters, bancos centrales y geopolítica...)
2. descarta lo ya visto y lo de hace más de 2,5 horas
3. Gemini puntúa de 1 a 10 y redacta solo las de 8 o más (9 o más de 00.00 a 07.00), buscando variedad de temas
4. limpia el formato (dos puntos, rayas, coletillas, negritas y emojis de más)
5. deja al menos 25 min entre publicaciones salvo noticias de última hora (9 o más), como mucho 2 por pasada y 15 al día

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
- `max_posts_per_run`, `daily_post_cap`, `min_minutes_between_posts` volumen y ritmo
- `breaking_score` puntuación a partir de la cual una noticia sale aunque se acabe de publicar otra
- `feeds` añadir o quitar fuentes
- `models` orden de modelos gratuitos de Gemini
- El estilo de redacción está en `prompt.md`

## Pausar

Actions → Alertas Bursátiles → menú `···` → Disable workflow.
