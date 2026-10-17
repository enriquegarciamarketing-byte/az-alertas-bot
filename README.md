# AZ Inversión · Alertas Bursátiles

Bot que publica noticias flash de actualidad de mercados en el canal de Telegram **@AZ_Inversion** con el estilo de AZ. Cubre macro, bancos centrales, geopolítica (Ormuz, Irán, aranceles...), materias primas, empresas y cripto. Titular en negrita con $TICKER o bandera/emoji de categoría y un solo párrafo, sin dos puntos ni coletillas.

Cada 15 minutos GitHub Actions MIRA las fuentes, pero solo publica cuando algo lo merece
1. lee 27 fuentes (CNBC, MarketWatch, Seeking Alpha, Nikkei Asia, CoinDesk, Investing, Expansión, Cinco Días, Europa Press, Google News con Reuters, bancos centrales y geopolítica...)
2. descarta lo ya visto y lo de hace más de 2,5 horas
3. Gemini puntúa cada noticia con la escala de importancia AZ (alcance 0-3, sorpresa 0-3, magnitud 0-2, impacto inmediato 0-2). El código suma los puntos y solo publica lo que llega a 9 sobre 10
4. limpia el formato (dos puntos, rayas, coletillas, negritas y emojis de más)
5. deja al menos 15 min entre publicaciones salvo un 10, como mucho 2 por pasada y 12 al día

Las noticias salen solo con texto, sin imagen ni fuente. En config.json, `"source_link": true` añade "🔗 Fuente [medio]" con la vista previa de la noticia original, y `"images": true` activa fotos de Pixabay.

Las noticias de acciones llevan $TICKER. El resto (macro, Fed, Ormuz, petróleo, cripto...) va sin ticker, con bandera o emoji de categoría.

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
| `PIXABAY_API_KEY` | opcional, fotos de reserva de Pixabay |
| `PEXELS_API_KEY` | opcional, segunda fuente de fotos si algún día tienes clave de Pexels |

## Probar

Actions → Alertas Bursátiles → Run workflow
- `test` publica ya la mejor noticia disponible en el canal
- `dry` no publica nada, enseña en el log lo que habría publicado
- `normal` igual que la ejecución automática

## Ajustes (config.json)

- `min_score`, `min_score_night` nota mínima en la escala AZ (9 por defecto; 10 = solo bombazos, 8 = algo más de volumen)
- `max_posts_per_run`, `daily_post_cap`, `min_minutes_between_posts` volumen y ritmo
- `breaking_score` nota a partir de la cual una noticia sale aunque se acabe de publicar otra
- `feeds` añadir o quitar fuentes
- `models` orden de modelos gratuitos de Gemini
- El estilo de redacción está en `prompt.md`

## Pausar

Actions → Alertas Bursátiles → menú `···` → Disable workflow.

## Brief diario (brief.py)

Todos los días a las 7.45 hora de España publica en el canal el brief de 8 posts, cada uno con su tarjeta AZ (1080x1350, generada por código con Barlow y el isotipo `az_logo.png`, sin imágenes de terceros).

1. La historia del día, con la cabecera AZ NEWS y el termómetro del VIX
2. El concepto del día
3-6. En contexto 1/4 a 4/4, de sectores distintos
7. Cómo te afecta, con S&P 500, Euríbor 12 meses y Brent (datos de Yahoo Finance y euribor-rates.eu, nunca inventados por el modelo)
8. Dato curioso

Gemini redacta con las reglas de `brief_prompt.md` a partir de las noticias de 24 h. `brief_state.json` guarda lo publicado para no repetir historia, protagonista, concepto (30 días) ni dato curioso.

Probar en Actions → Brief diario AZ News → Run workflow
- `dry` no publica, deja los 8 textos en el log y las 8 imágenes en Artifacts
- `test` publica ya en el canal
